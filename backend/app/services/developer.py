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
from app.models.generation import GenerationOutput, validate_manifest
from app.models.interview import StructuredRequirements
from app.services.spark_client import responses_structured

DEVELOPER_SYSTEM = """\
You are FIELD's developer. Implement the approved specification as a small \
React + Vite + TypeScript single-page app. Aim for at most 12 files.

FIXED SCAFFOLD (already exists — do NOT regenerate these):
- package.json: react 18, react-dom 18, vite 5, typescript 5.5, \
@vitejs/plugin-react. No other dependencies are installed. Import nothing else.
- src/main.tsx: renders the default export of ./App into #root.
- tsconfig: strict TypeScript, allowJs, jsx react-jsx. Your code MUST \
compile cleanly: type all props and state, no implicit any.
- index.html has <div id="root"> and loads /src/main.tsx.

YOUR FILES (relative paths under src/ only):
- src/App.tsx (REQUIRED): default-exported App component with the screens \
and workflows. Keep all state in React (useState); localStorage is allowed.
- src/app.css (optional): include ONLY if App.tsx imports "./app.css".
- src/lib/*.js (encouraged for core rules): plain JavaScript, no imports, \
imported by App.tsx (e.g. status-transition helpers). It may be executed \
directly by tests, so keep it dependency-free and correct.
- src/components/*.tsx, src/data/*.ts (optional): extra components, sample data.

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
  status (e.g. "Advance to Preparing").

RULES:
- Implement ONLY what the spec requires; map every feature to its \
requirement IDs in feature_map.
- No backend, no accounts, no payments, no network calls, no routing \
libraries, no process.env, no secrets, no child_process.
- If a requirement cannot be implemented in this constrained app, list it \
in unimplemented with the reason instead of faking it.
- Reply with ONLY valid DeveloperOutput JSON.
"""

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
The FIXED SCAFFOLD, YOUR FILES, ACCESSIBLE MARKUP CONTRACT, and RULES
below apply exactly as for a fresh build — you return the COMPLETE file
set (every CURRENT SOURCE file listed below, fixed and unchanged),
never a patch. Never return scaffold-owned files (src/main.tsx,
src/vite-env.d.ts): they exist but are FIELD-owned, not yours.

FIXED SCAFFOLD (already exists — do NOT regenerate these):
- package.json: react 18, react-dom 18, vite 5, typescript 5.5, \
@vitejs/plugin-react. No other dependencies are installed. Import nothing else.
- src/main.tsx: renders the default export of ./App into #root.
- tsconfig: strict TypeScript, allowJs, jsx react-jsx. Your code MUST \
compile cleanly: type all props and state, no implicit any.
- index.html has <div id="root"> and loads /src/main.tsx.

YOUR FILES (relative paths under src/ only):
- src/App.tsx (REQUIRED): default-exported App component with the screens \
and workflows. Keep all state in React (useState); localStorage is allowed.
- src/app.css (optional): include ONLY if App.tsx imports "./app.css".
- src/lib/*.js (encouraged for core rules): plain JavaScript, no imports, \
imported by App.tsx (e.g. status-transition helpers). It may be executed \
directly by tests, so keep it dependency-free and correct.
- src/components/*.tsx, src/data/*.ts (optional): extra components, sample data.

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
  status (e.g. "Advance to Preparing").

RULES:
- Implement ONLY what the spec requires; map every feature to its \
requirement IDs in feature_map.
- No backend, no accounts, no payments, no network calls, no routing \
libraries, no process.env, no secrets, no child_process.
- If a requirement cannot be implemented in this constrained app, list it \
in unimplemented with the reason instead of faking it.
- Fix ONLY the observed failures listed below; keep all working behavior.
- Address an audit finding ONLY when you can ground it in the provided \
sources or test results; never invent a fix for evidence you cannot see.
- Reply with ONLY valid DeveloperOutput JSON.
"""

#: Repair input budgets (mirrors the auditor evidence budgets).
REPAIR_FILE_BUDGET = 6000
REPAIR_TOTAL_BUDGET = 20000
REPAIR_FAILURES_BUDGET = 4000


class GenerationValidationError(ValueError):
    """The model output failed structural or manifest validation."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons) if reasons else "invalid generation output")


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
        max_output_tokens=16384,
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
            max_output_tokens=16384,
        )
        try:
            output = GenerationOutput.model_validate(data)
        except ValidationError as exc2:
            raise GenerationValidationError([f"malformed developer output: {exc2}"]) from exc2
    manifest_errors = validate_manifest(output.files)
    if manifest_errors:
        raise GenerationValidationError(manifest_errors)
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
        max_output_tokens=16384,
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
            max_output_tokens=16384,
        )
        try:
            output = GenerationOutput.model_validate(data)
        except ValidationError as exc2:
            raise GenerationValidationError([f"malformed developer output: {exc2}"]) from exc2
    manifest_errors = validate_manifest(output.files)
    if manifest_errors:
        raise GenerationValidationError(manifest_errors)
    return output, usage, get_settings().muse_spark_model_id
