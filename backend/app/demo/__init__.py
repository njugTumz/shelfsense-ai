"""Bundled demo fixtures: reference app and sample interview evidence."""

from app.demo.restaurant_app import (
    APP_ROOT,
    BUNDLE_EVIDENCE_STRINGS,
    DEMO_ACCEPTANCE_CRITERIA,
    DEMO_REQUIREMENTS,
    ORDER_LOGIC_TEST_MJS,
    demo_files,
)
from app.demo.restaurant_evidence import (
    DEMO_EVIDENCE_SOURCE,
    demo_observed_evidence,
)
from app.demo.scaffold import scaffold_files

__all__ = [
    "APP_ROOT",
    "BUNDLE_EVIDENCE_STRINGS",
    "DEMO_ACCEPTANCE_CRITERIA",
    "DEMO_EVIDENCE_SOURCE",
    "DEMO_REQUIREMENTS",
    "ORDER_LOGIC_TEST_MJS",
    "demo_files",
    "demo_observed_evidence",
    "scaffold_files",
]
