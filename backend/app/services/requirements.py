"""Structured requirements generation and explicit approval (plan.md §8-9).

The planner turns interview evidence into a specification concrete enough
to guide constrained React generation. Change requests regenerate the
spec (bumped revision) from the previous spec plus owner feedback.
Approval flattens the spec into the pipeline-facing ``requirements`` /
``acceptance_criteria`` fields and timestamps the decision.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.store import load_mission, save_mission, utc_now_iso
from app.models.interview import InterviewStatus, StructuredRequirements
from app.models.mission import Mission, MissionStatus
from app.services.interview import LifecycleError
from app.services.spark_client import responses_structured

#: Read budget for spec planning/generation (medium effort, 16k tokens).
REQUIREMENTS_TIMEOUT_S = 300.0

PLANNER_SYSTEM = """\
You are FIELD's planner. Turn the business evidence and interview answers \
into a concrete application specification for a constrained React/Vite/ \
TypeScript single-page app (no backend, no accounts, no payments).

Rules:
- Ground every requirement in the evidence; do not invent business facts.
- Functional requirements get stable ids (FR-1, FR-2, ...).
- Acceptance criteria must be TESTABLE: each one names an observable UI \
behavior or state change (e.g. "Staff can move an order from Received to \
Preparing and the list updates").
- User journeys list ordered steps a named role performs in the app.
- Data entities name the client-side state the app manages.
- Put guesses in assumptions (they will be shown as INFERRED, needing \
confirmation), and genuine unknowns in unresolved_questions.
- Reply with ONLY valid RequirementsSpec JSON.
"""

REQUIREMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "business_context": {"type": "string"},
        "problem_statement": {"type": "string"},
        "objectives": {"type": "array", "items": {"type": "string"}},
        "user_roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["name", "description"],
                "additionalProperties": False,
            },
        },
        "functional_requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "requirement": {"type": "string"}},
                "required": ["id", "requirement"],
                "additionalProperties": False,
            },
        },
        "non_functional_requirements": {"type": "array", "items": {"type": "string"}},
        "user_journeys": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "steps"],
                "additionalProperties": False,
            },
        },
        "core_features": {"type": "array", "items": {"type": "string"}},
        "data_entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "fields"],
                "additionalProperties": False,
            },
        },
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "business_context",
        "problem_statement",
        "objectives",
        "user_roles",
        "functional_requirements",
        "non_functional_requirements",
        "user_journeys",
        "core_features",
        "data_entities",
        "acceptance_criteria",
        "assumptions",
        "unresolved_questions",
    ],
    "additionalProperties": False,
}


def _render_input(mission: Mission, change_request: str | None) -> str:
    evidence = "\n".join(
        f"- [{item.category.value}] {item.text} (source: {item.source or 'unspecified'})"
        for item in mission.evidence
    )
    history = "\n".join(
        f"Q{turn.index}: {turn.question}\nA{turn.index}: {turn.answer}"
        for turn in mission.interview.turns
        if turn.answer.strip()
    )
    parts = [
        PLANNER_SYSTEM,
        f"EVIDENCE\n{evidence or '(none)'}",
        f"INTERVIEW ANSWERS\n{history or '(no answers recorded)'}",
    ]
    if mission.spec is not None and change_request:
        parts.append(f"PREVIOUS SPEC (revision {mission.spec.revision})\n{mission.spec.model_dump_json(indent=1)}")
        parts.append(
            f"OWNER CHANGE REQUEST (apply this and keep everything else unless it conflicts)\n{change_request}"
        )
    return "\n\n".join(parts)


def _generate_validated(input_text: str) -> StructuredRequirements:
    try:
        data, _usage = responses_structured(
            schema_name="RequirementsSpec",
            schema=REQUIREMENTS_SCHEMA,
            input_text=input_text,
            effort="medium",
            max_output_tokens=16384,
            timeout_s=REQUIREMENTS_TIMEOUT_S,
        )
        return StructuredRequirements.model_validate(data)
    except ValidationError as exc:
        data, _usage = responses_structured(
            schema_name="RequirementsSpec",
            schema=REQUIREMENTS_SCHEMA,
            input_text=(
                f"{input_text}\n\nYour previous reply did not match the "
                f"RequirementsSpec shape ({exc}). Reply again with ONLY valid "
                f"RequirementsSpec JSON."
            ),
            effort="medium",
            max_output_tokens=16384,
            timeout_s=REQUIREMENTS_TIMEOUT_S,
        )
        return StructuredRequirements.model_validate(data)


def generate_requirements(mission_id: str, change_request: str | None = None, db_path=None) -> Mission:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if not mission.interview.is_complete:
        raise LifecycleError("Requirements need a completed interview first.")
    feedback = (change_request or "").strip()
    if feedback and mission.spec is None:
        raise LifecycleError("There is no previous spec to change yet.")
    if len(feedback) > 8000:
        raise ValueError("change_request exceeds 8000 characters.")

    spec = _generate_validated(_render_input(mission, feedback or None))
    spec.revision = (mission.spec.revision + 1) if (feedback and mission.spec) else 1
    mission.spec = spec
    mission.status = MissionStatus.REQUIREMENTS_READY
    mission.error = ""
    return save_mission(mission, db_path)


def _flatten_requirements_text(spec: StructuredRequirements) -> str:
    roles = ", ".join(r.name for r in spec.user_roles)
    features = "\n".join(f"- {f}" for f in spec.core_features)
    return (
        f"{spec.problem_statement}\n\nUsers: {roles}.\n\nFeatures:\n{features or '(none listed)'}"
    )


def approve_requirements(mission_id: str, db_path=None) -> Mission:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if mission.status != MissionStatus.REQUIREMENTS_READY:
        raise LifecycleError(
            f"Approval needs REQUIREMENTS_READY (mission is {mission.status.value})."
        )
    if mission.spec is None:
        raise LifecycleError("Approval needs generated requirements first.")
    blockers = mission.spec.approval_blockers()
    if blockers:
        raise LifecycleError(f"Requirements are not approvable: {'; '.join(blockers)}.")

    mission.requirements = _flatten_requirements_text(mission.spec)
    mission.acceptance_criteria = list(mission.spec.acceptance_criteria)
    mission.status = MissionStatus.APPROVED
    mission.approved_at = utc_now_iso()
    mission.error = ""
    return save_mission(mission, db_path)
