"""FIELD backend services: E2B verification and auditor providers."""

from app.services.auditors import (
    ACTIVE_AUDITOR_NAME,
    TARGET_AUDITOR_NAME,
    AuditEvidence,
    Auditor,
    GlimmerAuditor,
    SparkAdversarialAuditor,
    active_auditor,
    get_auditor,
)
from app.services.e2b_verification import verify_in_e2b
from app.services.spark_client import (
    SparkError,
    SparkUnavailableError,
    responses_structured,
    responses_structured_multimodal,
)

__all__ = [
    "ACTIVE_AUDITOR_NAME",
    "TARGET_AUDITOR_NAME",
    "AuditEvidence",
    "Auditor",
    "GlimmerAuditor",
    "SparkAdversarialAuditor",
    "SparkError",
    "SparkUnavailableError",
    "active_auditor",
    "get_auditor",
    "responses_structured",
    "responses_structured_multimodal",
    "verify_in_e2b",
]
