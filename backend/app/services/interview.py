"""AI interviewer: grounded follow-up questions, max three (plan.md §8).

One question at a time, each with a rationale. The three-question cap is
enforced server-side regardless of what the model suggests. All calls go
through the shared Spark client; failures persist nothing, so the user
can retry with their text intact.
"""

from __future__ import annotations

from typing import Any

from app.core.store import load_mission, save_mission
from app.demo.restaurant_evidence import demo_observed_evidence
from app.models.interview import (
    EvidenceCategory,
    EvidenceItem,
    InterviewState,
    InterviewStatus,
    InterviewTurn,
)
from app.models.mission import Mission, MissionStatus
from app.services.spark_client import SparkError, responses_structured

#: Read budget for one interviewer turn (low effort, short output).
INTERVIEW_TIMEOUT_S = 180.0

MAX_INPUT_CHARS = 8000

INTERVIEWER_SYSTEM = """\
You are FIELD's interviewer. You help a business owner clarify what software \
they need. You have evidence (OBSERVED analysis output, STATED owner input, \
prior interview answers) and the questions already asked.

Rules:
- Ask ONE targeted question grounded in the evidence, or finish the interview.
- Ask at most what is still materially unclear; never re-ask answered topics.
- Never ask generic questions ("What features do you want?") when the \
evidence suggests specifics. Name what you noticed and ask about it.
- Explain briefly why you need this clarification (rationale).
- If nothing material remains unclear, set needs_more=false and leave the \
question empty.
- Reply with ONLY valid InterviewerTurn JSON.
"""

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "needs_more": {"type": "boolean"},
        "question": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["needs_more", "question", "rationale"],
    "additionalProperties": False,
}


class LifecycleError(ValueError):
    """The mission is not in a state that allows this transition."""


def _render_evidence(mission: Mission) -> str:
    lines = [
        f"- [{item.category.value}] {item.text} (source: {item.source or 'unspecified'})"
        for item in mission.evidence
    ]
    history = [
        f"Q{turn.index}: {turn.question}\nA{turn.index}: {turn.answer or '(unanswered)'}"
        for turn in mission.interview.turns
    ]
    return (
        f"EVIDENCE\n{chr(10).join(lines) or '(none)'}\n\n"
        f"INTERVIEW SO FAR ({mission.interview.answered_count} answered, "
        f"max {mission.interview.max_questions})\n{chr(10).join(history) or '(no questions yet)'}"
    )


def _ask_model(mission: Mission) -> tuple[bool, str, str]:
    data, _usage = responses_structured(
        schema_name="InterviewerTurn",
        schema=QUESTION_SCHEMA,
        input_text=f"{INTERVIEWER_SYSTEM}\n\n{_render_evidence(mission)}",
        effort="low",
        timeout_s=INTERVIEW_TIMEOUT_S,
    )
    try:
        return bool(data["needs_more"]), str(data.get("question", "")), str(data.get("rationale", ""))
    except (KeyError, TypeError) as exc:
        raise SparkError(f"Malformed interviewer output: {exc}") from exc


def _checked_text(value: str, label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{label} must not be empty.")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError(f"{label} exceeds {MAX_INPUT_CHARS} characters.")
    return text


def start_interview(
    mission_id: str,
    owner_context: str = "",
    use_demo_evidence: bool = False,
    db_path=None,
) -> Mission:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    has_media = mission.media is not None
    media_ready = has_media and mission.media.is_ready
    if use_demo_evidence and has_media:
        raise LifecycleError("This mission has an uploaded video; the demo fixture is refused.")
    if has_media and not media_ready:
        raise LifecycleError(
            f"Video analysis is {mission.media.status.value}; complete analysis before interviewing."
        )
    context = (owner_context or "").strip()
    if len(context) > MAX_INPUT_CHARS:
        raise ValueError(f"owner_context exceeds {MAX_INPUT_CHARS} characters.")

    evidence: list[EvidenceItem] = []
    if media_ready and mission.status == MissionStatus.ANALYZING:
        evidence.extend(mission.evidence)
    elif mission.status == MissionStatus.CREATED and not has_media:
        if not context and not use_demo_evidence:
            raise ValueError("Provide business context or set use_demo_evidence=true.")
        if use_demo_evidence:
            evidence.extend(demo_observed_evidence())
    else:
        raise LifecycleError(
            f"Interview can only start from CREATED or analyzed video (mission is {mission.status.value})."
        )
    if context:
        evidence.append(EvidenceItem(category=EvidenceCategory.STATED, text=context, source="owner context"))
    if not evidence:
        raise ValueError("No evidence available: upload a video or provide business context.")

    probe = Mission(**{**mission.model_dump(), "evidence": evidence, "interview": InterviewState(
        status=InterviewStatus.IN_PROGRESS, turns=[], max_questions=3)})
    needs_more, question, rationale = _ask_model(probe)
    if not needs_more or not question.strip():
        probe.interview.status = InterviewStatus.COMPLETE
    else:
        probe.interview.turns.append(InterviewTurn(index=1, question=question.strip(), rationale=rationale.strip()))
    probe.status = MissionStatus.INTERVIEWING
    probe.error = ""
    return save_mission(probe, db_path)


def answer_question(mission_id: str, answer: str, db_path=None) -> Mission:
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    if mission.status != MissionStatus.INTERVIEWING or mission.interview.status != InterviewStatus.IN_PROGRESS:
        raise LifecycleError("No interview is awaiting an answer on this mission.")
    pending = mission.interview.pending_turn
    if pending is None:
        raise LifecycleError("No pending question on this mission.")
    text = _checked_text(answer, "answer")

    pending.answer = text
    mission.evidence.append(
        EvidenceItem(category=EvidenceCategory.STATED, text=text, source=f"interview answer {pending.index}")
    )
    if mission.interview.answered_count >= mission.interview.max_questions:
        mission.interview.status = InterviewStatus.COMPLETE
        mission.error = ""
        return save_mission(mission, db_path)

    needs_more, question, rationale = _ask_model(mission)
    if not needs_more or not question.strip():
        mission.interview.status = InterviewStatus.COMPLETE
    else:
        mission.interview.turns.append(
            InterviewTurn(
                index=len(mission.interview.turns) + 1,
                question=question.strip(),
                rationale=rationale.strip(),
            )
        )
    mission.error = ""
    return save_mission(mission, db_path)
