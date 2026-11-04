"""Interview and structured-requirements contracts (plan.md sections 8-9).

Evidence discipline: OBSERVED items are directly supported by analysis
artifacts, STATED items were communicated by the owner, INFERRED items
are interpretations awaiting confirmation. An inference is never stored
as an observation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class EvidenceCategory(str, Enum):
    OBSERVED = "OBSERVED"
    STATED = "STATED"
    INFERRED = "INFERRED"


class EvidenceItem(BaseModel):
    category: EvidenceCategory
    text: str
    source: str = ""


class InterviewStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


class InterviewTurn(BaseModel):
    """One question/answer round. Empty answer means awaiting the user."""

    index: int
    question: str
    rationale: str = ""
    answer: str = ""


class InterviewState(BaseModel):
    status: InterviewStatus = InterviewStatus.NOT_STARTED
    turns: list[InterviewTurn] = Field(default_factory=list)
    max_questions: int = 3

    @property
    def answered_count(self) -> int:
        return sum(1 for t in self.turns if t.answer.strip())

    @property
    def pending_turn(self) -> InterviewTurn | None:
        for turn in self.turns:
            if not turn.answer.strip():
                return turn
        return None

    @property
    def is_complete(self) -> bool:
        return self.status == InterviewStatus.COMPLETE


class UserRole(BaseModel):
    name: str
    description: str = ""


class FunctionalRequirement(BaseModel):
    id: str
    requirement: str


class UserJourney(BaseModel):
    name: str
    steps: list[str] = Field(default_factory=list)


class DataEntity(BaseModel):
    name: str
    fields: list[str] = Field(default_factory=list)


class StructuredRequirements(BaseModel):
    """Persisted application specification. Specific enough to guide
    constrained React generation; acceptance criteria must be testable."""

    business_context: str = ""
    problem_statement: str
    objectives: list[str] = Field(default_factory=list)
    user_roles: list[UserRole] = Field(default_factory=list)
    functional_requirements: list[FunctionalRequirement] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    user_journeys: list[UserJourney] = Field(default_factory=list)
    core_features: list[str] = Field(default_factory=list)
    data_entities: list[DataEntity] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    revision: int = 1

    @field_validator("problem_statement")
    @classmethod
    def _non_empty_problem(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("problem_statement must not be empty")
        return value

    def approval_blockers(self) -> list[str]:
        """Reasons this spec cannot be approved (empty = approvable)."""
        blockers = []
        if not self.user_roles:
            blockers.append("at least one user role is required")
        if not self.functional_requirements:
            blockers.append("at least one functional requirement is required")
        if not self.acceptance_criteria:
            blockers.append("at least one acceptance criterion is required")
        return blockers
