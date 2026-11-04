"""Mission lifecycle states and mission record (plan.md section 6)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, computed_field

from app.models.generation import GenerationRecord, RepairRecord
from app.models.interview import (
    EvidenceItem,
    InterviewState,
    StructuredRequirements,
)
from app.models.media import MediaRecord
from app.models.verification import VerificationResult


class MissionStatus(str, Enum):
    """Plan section 6 state machine plus the smallest verification set.

    VERIFIED_WITH_WARNINGS and AUDIT_LIMITED are the only additions the
    VERIFY stage needs; FAILED captures which stage failed via ``error``.
    """

    CREATED = "CREATED"
    VIDEO_UPLOADED = "VIDEO_UPLOADED"
    ANALYZING = "ANALYZING"
    INTERVIEWING = "INTERVIEWING"
    REQUIREMENTS_READY = "REQUIREMENTS_READY"
    APPROVED = "APPROVED"
    BUILDING = "BUILDING"
    RUNNING = "RUNNING"
    AUDITING = "AUDITING"
    VERIFIED = "VERIFIED"
    VERIFIED_WITH_WARNINGS = "VERIFIED_WITH_WARNINGS"
    AUDIT_LIMITED = "AUDIT_LIMITED"
    FAILED = "FAILED"
    PRESENTED = "PRESENTED"


class Mission(BaseModel):
    """Persisted mission with its verification evidence."""

    id: str
    name: str = ""
    status: MissionStatus = MissionStatus.CREATED
    created_at: str = ""
    updated_at: str = ""
    requirements: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    files: dict[str, str] | None = None
    verification: VerificationResult | None = None
    error: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    interview: InterviewState = Field(default_factory=InterviewState)
    spec: StructuredRequirements | None = None
    approved_at: str = ""
    generation: GenerationRecord | None = None
    repair: RepairRecord | None = None
    media: MediaRecord | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_approved(self) -> bool:
        return self.status == MissionStatus.APPROVED and bool(self.approved_at)
