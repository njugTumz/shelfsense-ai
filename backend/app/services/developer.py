"""Constrained developer agent: approved spec -> React file manifest.

FIELD owns the scaffold (package.json, configs, entry point); Spark
generates ONLY app source under ``src/``. Output is validated as
structured JSON, then every path and byte passes ``validate_manifest``
before anything reaches E2B. The model provides no shell commands:
E2B execution uses FIELD-fixed commands only.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.generation import (
    MAX_FILE_CHARS,
    GenerationOutput,
    validate_manifest,
)
from app.models.interview import StructuredRequirements
from app.services.spark_client import responses_structured

#: Output budget for code generation. High-effort reasoning tokens share
#: this budget with the emitted files, so a full file set needs headroom
#: above the 16k that truncated a production build (char ~6328 cut).
DEVELOPER_MAX_OUTPUT_TOKENS = 32768

#: Shared by the build and repair prompts so both describe one scaffold.
SCAFFOLD_SPEC = """\
FIXED SCAFFOLD (already exists — do NOT regenerate these):
- package.json: react 18, react-dom 18, lucide-react (pinned), vite 5, \
typescript 5.5, @vitejs/plugin-react, tailwindcss v3 + postcss + \
autoprefixer (pinned). No other dependencies are installed: import only \
react, react-dom, lucide-react, and relative paths. Anything else is rejected.
- src/main.tsx: imports ./index.css, renders the default export of ./App \
into #root.
- src/index.css: Tailwind directives (@tailwind base/components/utilities). \
Style with Tailwind utility classes in className; do not regenerate this file.
- tailwind.config.js + postcss.config.js: standard Tailwind v3 setup \
scanning index.html and src/**. Use v3 utility syntax only.
- tsconfig: strict TypeScript, allowJs, jsx react-jsx. Your code MUST \
compile cleanly: type all props and state, no implicit any.
- index.html has <div id="root"> and loads /src/main.tsx."""

#: Shared engineering bar for build and repair. Proportionate modular
#: architecture: strict boundaries, no enterprise ceremony.
ARCHITECTURE_CONTRACT = """\
ARCHITECTURE CONTRACT (small, modular, honest code):
- src/lib/* holds domain rules + storage as pure modules: `import type` \
and relative imports INSIDE src/lib/* are allowed (storage imports the \
rules); bare packages, require(), and anything outside lib are rejected \
(this is enforced). The rules themselves import no values.
- ONE transition gate owns every state change: a canTransition(from, to) \
function returning true only for a permitted transition. Every button, \
dropdown, and handler must consult it; invalid attempts are rejected, the \
original state is preserved, and the user sees clear feedback. Never offer \
an action the gate would reject without also enforcing the gate underneath.
- Constrained domain types: states are string-union types (e.g. type \
Status = "Received" | "Preparing"), never bare strings. Derive option \
lists and labels from the same constants the gate uses.
- Persistence boundary: one src/lib/storage module owns load/save/validate; \
components never touch localStorage directly and never persist rejected data.
- Modules stay focused: one screen or panel per component file, shared \
constants in src/data/*.ts, no giant App.tsx, no duplicated logic, no \
circular imports, no global state beyond the top-level component."""

#: Shared design bar for build and repair. Warm and professional by
#: default; adapt the palette to the business, never to a generic template.
DESIGN_CONTRACT = """\
DESIGN CONTRACT (the result must look built by a professional product team):
- Derive the screens from the spec's roles, journeys, and entities — a \
restaurant gets order/kitchen/shelf views, a retailer gets \
products/sales/stock, and so on. Never emit a generic dashboard.
- Warm, sophisticated palette: warm-neutral surfaces (stone), one \
coherent accent family (terracotta orange, amber, or moss green) with \
real text contrast. No purple/blue gradients, no emoji icons.
- Tailwind-first: rounded cards, consistent spacing scale, clear type \
hierarchy (one page title, section headings, body text), subtle \
borders/shadows, Lucide icons via lucide-react aligned with their labels.
- Responsive mobile-first layouts: single column on phones, grids and \
side-by-side panels from md: up. No horizontal overflow at 390px wide.
- Every control works: validated forms, useful empty/loading/error \
states, no dead buttons, no placeholder links, no lorem ipsum."""

ACCESSIBLE_MARKUP_CONTRACT = """\
ACCESSIBLE MARKUP CONTRACT (the app is tested by a real browser using
accessible roles, so follow this exactly):
- Order creation is a <form> containing one labeled text <input> and one
  submit <button> (e.g. "Add Order").
- If the spec needs extra inputs (details, type), keep them optional or
  pre-filled: submitting the form with every visible text field filled
  MUST immediately create an order whose <li> shows the entered text.
- Orders render as <ul>/<li> items; each item shows its id, its items
  text, and its current status text.
- Each order's status control is either a <label> + <select> with the
  status options, or a <button> whose accessible name states the next
  status (e.g. "Advance to Preparing")."""

ARCHITECTURE_RULES = """\
ARCHITECTURE (frontend-only application):
- Keep all state in React (useState); localStorage is allowed only when \
the spec's persistence need is genuinely single-device.
- No backend, no accounts, no payments, no network calls, no routing \
libraries, no process.env, no secrets, no child_process.
- If a requirement genuinely needs server-side behavior (shared \
multi-user data, accounts, real database), still build the best \
frontend-only prototype AND list that requirement ID in unimplemented \
with a reason starting "NEEDS_BACKEND: " describing what the server \
must provide; add an assumption stating the prototype's limits. Never \
claim localStorage is a production database."""

DEVELOPER_SYSTEM = """\
You are FIELD's developer. Implement the approved specification as a small, \
polished React + Vite + TypeScript + Tailwind CSS single-page app. Aim for \
at most 12 files.

""" + SCAFFOLD_SPEC + """

YOUR FILES (relative paths under src/ only):
- src/App.tsx (REQUIRED): default-exported App component composing the \
screens and workflows from the components below.
- src/components/*.tsx (encouraged): one focused component per screen or \
panel (e.g. OrderBoard, KitchenView, ShelfView); App.tsx composes them.
- src/lib/storage.* (expected when the app persists): load/save/validate \
helpers isolating localStorage behind the persistence boundary.
- src/lib/*.test.* (never): no test files; FIELD verifies with real browsers.

""" + ARCHITECTURE_CONTRACT + """
- src/lib/*.js (encouraged for core rules): plain JavaScript following the \
ARCHITECTURE CONTRACT above (e.g. status-transition helpers). It may be \
executed directly by tests, so keep it correct.
- src/data/*.ts (optional): sample data and constants.
- src/app.css (discouraged): Tailwind utilities cover styling; include a \
custom stylesheet ONLY for keyframes or print rules App.tsx cannot \
express, and import it explicitly.

""" + DESIGN_CONTRACT + """

""" + ACCESSIBLE_MARKUP_CONTRACT + """

RULES:
- Implement ONLY what the spec requires; map every feature to its \
requirement IDs in feature_map.
""" + ARCHITECTURE_RULES + """
- If a requirement cannot be implemented in this constrained app, list it \
in unimplemented with the reason instead of faking it.
- Reply with ONLY valid DeveloperOutput JSON."""

DEVELOPER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
        "feature_map": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "feature": {"type": "string"},
                    "requirement_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["feature", "requirement_ids"],
                "additionalProperties": False,
            },
        },
        "unimplemented": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["requirement_id", "reason"],
                "additionalProperties": False,
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "files", "feature_map", "unimplemented", "assumptions"],
    "additionalProperties": False,
}


REPAIR_SYSTEM = """\
You are FIELD's developer, repairing an app that failed verification.
The FIXED SCAFFOLD, YOUR FILES, DESIGN CONTRACT, ACCESSIBLE MARKUP
CONTRACT, and RULES below apply exactly as for a fresh build — you
return the COMPLETE file set (every CURRENT SOURCE file listed below,
fixed and unchanged), never a patch. Never return scaffold-owned files
(src/main.tsx, src/vite-env.d.ts, src/index.css): they exist but are
FIELD-owned, not yours.

""" + SCAFFOLD_SPEC + """

YOUR FILES (relative paths under src/ only):
- src/App.tsx (REQUIRED): default-exported App component composing the \
screens and workflows from the components below.
- src/components/*.tsx (encouraged): one focused component per screen or \
panel; App.tsx composes them.
- src/lib/storage.* (expected when the app persists): load/save/validate \
helpers isolating localStorage behind the persistence boundary.

""" + ARCHITECTURE_CONTRACT + """
- src/lib/*.js (encouraged for core rules): plain JavaScript following the \
ARCHITECTURE CONTRACT above. It may be executed directly by tests, so \
keep it correct.
- src/data/*.ts (optional): sample data and constants.
- src/app.css (discouraged): Tailwind utilities cover styling; include a \
custom stylesheet ONLY for keyframes or print rules App.tsx cannot \
express, and import it explicitly.

""" + DESIGN_CONTRACT + """

""" + ACCESSIBLE_MARKUP_CONTRACT + """

RULES:
- Implement ONLY what the spec requires; map every feature to its \
requirement IDs in feature_map.
""" + ARCHITECTURE_RULES + """
- If a requirement cannot be implemented in this constrained app, list it \
in unimplemented with the reason instead of faking it.
- Fix ONLY the observed failures listed below; keep all working behavior.
- A repair may improve styling toward the DESIGN CONTRACT only insofar \
as the failures demand it; never redecorate working screens.
- Address an audit finding ONLY when you can ground it in the provided \
sources or test results; never invent a fix for evidence you cannot see.
- Reply with ONLY valid DeveloperOutput JSON."""

#: Repair input budgets (mirrors the auditor evidence budgets).
REPAIR_FILE_BUDGET = 6000
REPAIR_TOTAL_BUDGET = 20000
REPAIR_FAILURES_BUDGET = 4000


class GenerationValidationError(ValueError):
    """The model output failed structural or manifest validation."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons) if reasons else "invalid generation output")


def _oversized_files(files: list[GeneratedFile]) -> list[tuple[str, int]]:
    return [(f.path, len(f.content)) for f in files if len(f.content) > MAX_FILE_CHARS]


def _recover_oversized_manifest(
    input_text: str,
    files: list[GeneratedFile],
    caller,
) -> tuple[GenerationOutput | None, dict[str, int] | None]:
    """One bounded recovery attempt for oversized files.

    Returns (None, None) when no file exceeds the limit (caller raises its
    original errors). Otherwise makes exactly one retry asking for modular
    decomposition, validates the WHOLE returned manifest, and returns the
    replacement output. Never truncates, splits blindly, drops features,
    raises limits, or retries more than once.
    """
    oversized = _oversized_files(files)
    if not oversized:
        return None, None
    listing = ", ".join(f"{path} ({size} chars)" for path, size in oversized)
    data, usage = caller(
        f"{input_text}\n\nYour previous reply was REJECTED for oversized files: "
        f"{listing} (limit {MAX_FILE_CHARS} chars each). Do NOT truncate, "
        f"split at arbitrary positions, or drop features. Decompose each "
        f"oversized file into smaller focused modules (components, lib, "
        f"data) preserving every requirement and feature, and return the "
        f"COMPLETE corrected manifest with ONLY valid DeveloperOutput JSON."
    )
    try:
        retry_output = GenerationOutput.model_validate(data)
    except ValidationError as exc:
        raise GenerationValidationError(
            [f"malformed recovery output: {exc}"]
        ) from exc
    retry_errors = validate_manifest(retry_output.files)
    if retry_errors:
        raise GenerationValidationError(retry_errors)
    return retry_output, usage


def _render_spec(spec: StructuredRequirements) -> str:
    roles = "\n".join(f"- {r.name}: {r.description}" for r in spec.user_roles)
    frs = "\n".join(f"- {f.id}: {f.requirement}" for f in spec.functional_requirements)
    journeys = "\n".join(
        f"- {j.name}: {' > '.join(j.steps)}" for j in spec.user_journeys
    )
    entities = "\n".join(f"- {e.name} ({', '.join(e.fields)})" for e in spec.data_entities)
    criteria = "\n".join(f"- {c}" for c in spec.acceptance_criteria)
    return (
        f"BUSINESS CONTEXT\n{spec.business_context}\n\n"
        f"PROBLEM\n{spec.problem_statement}\n\n"
        f"USER ROLES\n{roles}\n\n"
        f"FUNCTIONAL REQUIREMENTS\n{frs}\n\n"
        f"CORE FEATURES\n{chr(10).join(f'- {f}' for f in spec.core_features)}\n\n"
        f"USER JOURNEYS\n{journeys}\n\n"
        f"DATA ENTITIES\n{entities}\n\n"
        f"ACCEPTANCE CRITERIA\n{criteria}\n\n"
        f"APPROVED ASSUMPTIONS\n{chr(10).join(f'- {a}' for a in spec.assumptions)}"
    )


def generate_application(
    spec: StructuredRequirements,
) -> tuple[GenerationOutput, dict[str, int], str]:
    """Run the developer agent. Returns (validated output, usage, model_id).

    Raises SparkError when the provider call fails, or
    GenerationValidationError when the output fails schema/manifest rules.
    """
    input_text = f"{DEVELOPER_SYSTEM}\n\nAPPROVED SPECIFICATION\n{_render_spec(spec)}"
    data, usage = responses_structured(
        schema_name="DeveloperOutput",
        schema=DEVELOPER_SCHEMA,
        input_text=input_text,
        effort="high",
        max_output_tokens=DEVELOPER_MAX_OUTPUT_TOKENS,
    )
    try:
        output = GenerationOutput.model_validate(data)
    except ValidationError as exc:
        data, usage = responses_structured(
            schema_name="DeveloperOutput",
            schema=DEVELOPER_SCHEMA,
            input_text=(
                f"{input_text}\n\nYour previous reply did not match the "
                f"DeveloperOutput shape ({exc}). Reply again with ONLY valid "
                f"DeveloperOutput JSON."
            ),
            effort="high",
            max_output_tokens=DEVELOPER_MAX_OUTPUT_TOKENS,
        )
        try:
            output = GenerationOutput.model_validate(data)
        except ValidationError as exc2:
            raise GenerationValidationError([f"malformed developer output: {exc2}"]) from exc2
    manifest_errors = validate_manifest(output.files)
    if manifest_errors:
        recovered, retry_usage = _recover_oversized_manifest(
            input_text,
            output.files,
            lambda text: responses_structured(
                schema_name="DeveloperOutput",
                schema=DEVELOPER_SCHEMA,
                input_text=text,
                effort="high",
                max_output_tokens=DEVELOPER_MAX_OUTPUT_TOKENS,
            ),
        )
        if recovered is None:
            raise GenerationValidationError(manifest_errors)
        output, usage = recovered, retry_usage
    return output, usage, get_settings().muse_spark_model_id


def _render_current_sources(current_files: dict[str, str]) -> str:
    ordered = sorted(current_files.items(), key=lambda kv: (kv[0] != "src/App.tsx", kv[0]))
    chunks: list[str] = []
    total = 0
    for path, content in ordered:
        snippet = content[:REPAIR_FILE_BUDGET]
        if total + len(snippet) > REPAIR_TOTAL_BUDGET:
            chunks.append(f"--- {path} (omitted: repair budget reached) ---")
            continue
        total += len(snippet)
        truncated = " [truncated]" if len(content) > REPAIR_FILE_BUDGET else ""
        chunks.append(f"--- {path}{truncated} ---\n{snippet}")
    return "\n\n".join(chunks) or "(no current sources provided)"


def repair_application_files(
    spec: StructuredRequirements,
    current_files: dict[str, str],
    failures: str,
) -> tuple[GenerationOutput, dict[str, int], str]:
    """Run the developer agent in repair mode. Returns (output, usage, model_id).

    ``current_files`` maps generated short paths (``src/...``) to their
    current content; ``failures`` is the observed failure evidence
    (failed checks and/or blocking audit findings). Output passes the
    same schema and manifest validation as a fresh build.

    Raises SparkError when the provider call fails, or
    GenerationValidationError when the output fails schema/manifest rules.
    """
    input_text = (
        f"{REPAIR_SYSTEM}\n\nAPPROVED SPECIFICATION\n{_render_spec(spec)}\n\n"
        f"CURRENT SOURCES (fix these; return the complete set)\n"
        f"{_render_current_sources(current_files)}\n\n"
        f"OBSERVED FAILURES (fix only these)\n{failures[:REPAIR_FAILURES_BUDGET]}"
    )
    data, usage = responses_structured(
        schema_name="DeveloperOutput",
        schema=DEVELOPER_SCHEMA,
        input_text=input_text,
        effort="high",
        max_output_tokens=DEVELOPER_MAX_OUTPUT_TOKENS,
    )
    try:
        output = GenerationOutput.model_validate(data)
    except ValidationError as exc:
        data, usage = responses_structured(
            schema_name="DeveloperOutput",
            schema=DEVELOPER_SCHEMA,
            input_text=(
                f"{input_text}\n\nYour previous reply did not match the "
                f"DeveloperOutput shape ({exc}). Reply again with ONLY valid "
                f"DeveloperOutput JSON."
            ),
            effort="high",
            max_output_tokens=DEVELOPER_MAX_OUTPUT_TOKENS,
        )
        try:
            output = GenerationOutput.model_validate(data)
        except ValidationError as exc2:
            raise GenerationValidationError([f"malformed developer output: {exc2}"]) from exc2
    manifest_errors = validate_manifest(output.files)
    if manifest_errors:
        recovered, retry_usage = _recover_oversized_manifest(
            input_text,
            output.files,
            lambda text: responses_structured(
                schema_name="DeveloperOutput",
                schema=DEVELOPER_SCHEMA,
                input_text=text,
                effort="high",
                max_output_tokens=DEVELOPER_MAX_OUTPUT_TOKENS,
            ),
        )
        if recovered is None:
            raise GenerationValidationError(manifest_errors)
        output, usage = recovered, retry_usage
    return output, usage, get_settings().muse_spark_model_id
