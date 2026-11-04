"""Video media and evidence contracts for the upload->analysis pipeline.

MediaRecord is the one structured evidence object: video metadata plus
timestamped frames, transcript turns, and frame-referenced visual
observations. Synthesis converts it into categorized mission evidence
(OBSERVED from frames, STATED from transcript, INFERRED from model
hypotheses) — never the reverse.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MediaStatus(str, Enum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"


class FrameRecord(BaseModel):
    id: str
    timestamp_s: float
    path: str = ""
    width: int = 0
    height: int = 0


class TranscriptTurn(BaseModel):
    index: int
    start_ms: int = 0
    end_ms: int = 0
    transcript: str = ""
    speaker: str = ""


class TranscriptRecord(BaseModel):
    text: str = ""
    turns: list[TranscriptTurn] = Field(default_factory=list)
    audio_duration_ms: int = 0
    model_id: str = ""


class VisualObservation(BaseModel):
    observation: str
    frame_ids: list[str] = Field(default_factory=list)
    confidence: str = ""


class VisualHypothesis(BaseModel):
    hypothesis: str
    basis_frame_ids: list[str] = Field(default_factory=list)
    confidence: str = ""


class MediaRecord(BaseModel):
    """Persisted video analysis state and artifacts for one mission.

    File paths are relative to the media storage root and resolved at
    runtime; only extracted frames are ever served, via a mission-scoped
    API route. Raw uploads are never publicly exposed.
    """

    mission_id: str
    original_filename: str = ""
    video_path: str = ""
    content_type: str = ""
    size_bytes: int = 0
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    status: MediaStatus = MediaStatus.UPLOADED
    error: str = ""
    frames: list[FrameRecord] = Field(default_factory=list)
    transcript: TranscriptRecord | None = None
    observations: list[VisualObservation] = Field(default_factory=list)
    hypotheses: list[VisualHypothesis] = Field(default_factory=list)
    vision_model_id: str = ""
    vision_usage: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_ready(self) -> bool:
        return self.status == MediaStatus.READY
