"""Constrained generation contracts and manifest validator.

FIELD owns the scaffold (package.json, configs, entry point); the model
generates ONLY application source under ``src/``. Every path and byte is
validated here before anything reaches E2B. The validator is pure (no
I/O) so all rejection rules are keyless-testable.
"""

from __future__ import annotations

import posixpath
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

MAX_FILES = 25
MAX_TOTAL_CHARS = 120_000
MAX_FILE_CHARS = 20_000
MAX_PATH_DEPTH = 4

ALLOWED_ROOT = "src/"
ALLOWED_EXTENSIONS = frozenset({".tsx", ".ts", ".js", ".css"})
BLOCKED_FILES = frozenset({"src/main.tsx", "src/vite-env.d.ts", "src/index.css"})
REQUIRED_FILES = frozenset({"src/App.tsx"})

#: Bare (non-relative) imports the model may use: exactly the scaffold's
#: installed dependencies. Relative imports (./, ../) are always allowed.
ALLOWED_BARE_IMPORTS = frozenset({"react", "react-dom", "lucide-react"})

#: Generated code must never touch host secrets or spawn processes.
#: (E2B commands are FIELD-fixed; the model provides no shell commands.)
FORBIDDEN_CONTENT = (
    "process.env",
    "import.meta.env",
    "META_MODEL_API_KEY",
    "E2B_API_KEY",
    "MODAL_API_KEY",
    "AZURE_SPEECH",
    "child_process",
)


class GenerationStatus(str, Enum):
    GENERATING = "generating"
    GENERATED = "generated"
    FAILED = "failed"


class GeneratedFile(BaseModel):
    path: str
    content: str


class FeatureMapping(BaseModel):
    feature: str
    requirement_ids: list[str] = Field(default_factory=list)


class UnimplementedItem(BaseModel):
    requirement_id: str
    reason: str = ""


class GenerationOutput(BaseModel):
    """Validated developer-agent output (structured Spark response)."""

    summary: str
    files: list[GeneratedFile] = Field(min_length=1)
    feature_map: list[FeatureMapping] = Field(default_factory=list)
    unimplemented: list[UnimplementedItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class GenerationRecord(BaseModel):
    """Persisted generation outcome for one mission."""

    status: GenerationStatus
    started_at: str = ""
    completed_at: str = ""
    model_id: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    manifest: list[str] = Field(default_factory=list)
    feature_map: list[FeatureMapping] = Field(default_factory=list)
    unimplemented: list[UnimplementedItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    error: str = ""
    error_class: str = ""


class RepairStatus(str, Enum):
    REPAIRING = "repairing"
    REPAIRED = "repaired"
    FAILED = "failed"


class RepairRecord(BaseModel):
    """Persisted repair-and-retest outcome (plan.md sections 10-11).

    The MVP allows exactly one completed repair+retest cycle per
    mission: ``attempts`` counts retests that ran. A repair whose
    generation failed before any retest keeps ``attempts`` at 0 so a
    transient provider error does not consume the single attempt.
    """

    status: RepairStatus
    attempts: int = 0
    trigger: str = ""
    previous_status: str = ""
    previous_reason: str = ""
    new_status: str = ""
    new_reason: str = ""
    summary: str = ""
    manifest: list[str] = Field(default_factory=list)
    model_id: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""
    error: str = ""


class RequirementCoverageStatus(str, Enum):
    IMPLEMENTED = "implemented"
    UNIMPLEMENTED = "unimplemented"
    MISSING_EVIDENCE = "missing_evidence"


class RequirementCoverage(BaseModel):
    requirement_id: str
    requirement: str
    status: RequirementCoverageStatus
    files: list[str] = Field(default_factory=list)
    detail: str = ""


class AcceptanceSupport(str, Enum):
    SUPPORTED = "supported"
    UNVERIFIED = "unverified"


class AcceptanceMapping(BaseModel):
    criterion: str
    status: AcceptanceSupport
    by_checks: list[str] = Field(default_factory=list)
    detail: str = ""


class ArchitectureClass(str, Enum):
    FRONTEND_ONLY = "frontend_only"
    LOCAL_PERSISTENCE = "local_persistence"
    NEEDS_BACKEND = "needs_backend"
    NEEDS_INTEGRATION = "needs_integration"


class ArchitectureClassification(BaseModel):
    """Deterministic architecture verdict with honest prototype limits."""

    category: ArchitectureClass
    reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CoverageReport(BaseModel):
    """How verification evidence maps to the approved spec. Static source
    evidence and executed E2B checks are labeled as such; anything else
    is UNVERIFIED, never a pass."""

    requirements: list[RequirementCoverage] = Field(default_factory=list)
    acceptance: list[AcceptanceMapping] = Field(default_factory=list)
    architecture: ArchitectureClassification | None = None


_DECLARATION_KEYWORDS = r"(?:function|const|let|var|default|async|class|interface|enum|abstract|declare)\b"
_IMPORT_SPEC = re.compile(
    r"""(?:^|\n)\s*import\s+(type\s+)?(?:[^"';]*?\s+from\s+)?["']([^"']+)["']"""
    rf"""|(?:^|\n)\s*export\s+(?!{_DECLARATION_KEYWORDS})(type\s+)?(?:[^"';]*?\s+from\s+)?["']([^"']+)["']"""
    r"""|require\(\s*["']([^"']+)["']\s*\)"""
    r"""|\bimport\(\s*["']([^"']+)["']\s*\)"""
)


def _disallowed_imports(content: str) -> list[str]:
    """Bare import specifiers outside the scaffold's installed dependencies.

    Type-only imports count too: tsc still resolves the package, so an
    uninstalled package breaks the build even for `import type`.
    """
    bad: list[str] = []
    for match in _IMPORT_SPEC.finditer(content):
        spec = match.group(2) or match.group(4) or match.group(5) or match.group(6) or ""
        if not spec or spec.startswith((".", "/")):
            continue
        root = spec.split("/")[0]
        if root not in ALLOWED_BARE_IMPORTS and spec not in bad:
            bad.append(spec)
    return bad


def _lib_purity_violation(path: str, content: str) -> str | None:
    """Rejection reason when a src/lib/* module breaks dependency purity.

    Allowed: `import type` (erased at runtime) and relative value imports
    resolving inside src/lib/* (storage importing the domain rules).
    Rejected: bare packages, require(), and anything reaching outside lib.
    """
    base = posixpath.dirname(path)
    for match in _IMPORT_SPEC.finditer(content):
        if match.group(1) or match.group(3):
            continue  # pure type import: erased at runtime, allowed in lib
        spec = match.group(2) or match.group(4) or match.group(5) or match.group(6) or ""
        if match.group(5) is not None:
            return f"lib purity violation: require() is not allowed in src/lib/*: {path}"
        if spec.startswith("."):
            resolved = posixpath.normpath(posixpath.join(base, spec))
            if resolved == "src/lib" or resolved.startswith("src/lib/"):
                continue
        return (
            "lib purity violation: src/lib/* allows only `import type` and "
            f"relative imports inside src/lib/*: {path} imports {spec!r}"
        )
    return None


def validate_manifest(files: list[GeneratedFile]) -> list[str]:
    """Return rejection reasons (empty = accepted)."""
    errors: list[str] = []
    if len(files) > MAX_FILES:
        errors.append(f"too many files: {len(files)} (max {MAX_FILES})")
    total = sum(len(f.content) for f in files)
    if total > MAX_TOTAL_CHARS:
        errors.append(f"generated output too large: {total} chars (max {MAX_TOTAL_CHARS})")
    seen: set[str] = set()
    for item in files:
        path = (item.path or "").strip()
        label = path or "(empty path)"
        if not path:
            errors.append("empty file path")
            continue
        if path in seen:
            errors.append(f"duplicate path: {label}")
        seen.add(path)
        if path.startswith("/") or ".." in path.split("/") or "\\" in path:
            errors.append(f"unsafe path (absolute/traversal): {label}")
            continue
        if not path.startswith(ALLOWED_ROOT):
            errors.append(f"path outside {ALLOWED_ROOT}: {label}")
            continue
        if len(path.split("/")) > MAX_PATH_DEPTH:
            errors.append(f"path too deep: {label}")
        ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"disallowed extension '{ext or '(none)'}': {label}")
        if path in BLOCKED_FILES:
            errors.append(f"scaffold-owned file must not be generated: {label}")
        if len(item.content) > MAX_FILE_CHARS:
            errors.append(f"file too large ({len(item.content)} chars): {label}")
        if not item.content.strip():
            errors.append(f"empty content: {label}")
        for forbidden in FORBIDDEN_CONTENT:
            if forbidden in item.content:
                errors.append(f"forbidden content '{forbidden}': {label}")
        for bad_import in _disallowed_imports(item.content):
            errors.append(f"disallowed import '{bad_import}' (FIELD installs only "
                          f"{sorted(ALLOWED_BARE_IMPORTS)}): {label}")
        if path.startswith("src/lib/"):
            violation = _lib_purity_violation(path, item.content)
            if violation:
                errors.append(violation)
    provided = {f.path.strip() for f in files}
    for required in REQUIRED_FILES:
        if required not in provided:
            errors.append(f"missing required file: {required}")
    return errors
