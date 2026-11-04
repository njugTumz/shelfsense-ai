"""Demo evidence fixture for the interview workflow.

The video ingestion pipeline is NOT connected yet, so the interview demo
grounds itself in these explicitly labeled sample observations plus
whatever context the owner types. Anything built from this fixture must
keep the demo label visible; never present these as real video analysis.
"""

from __future__ import annotations

from app.models.interview import EvidenceCategory, EvidenceItem

DEMO_EVIDENCE_SOURCE = "demo fixture (sample observations — video pipeline not connected)"

DEMO_OBSERVED_TEXTS = [
    "A counter area is visible where staff receive orders from customers.",
    "Orders appear to be written down manually at the counter.",
    "Kitchen staff seem to rely on verbal communication to learn about new orders.",
    "A pickup shelf is visible where completed orders wait for customers.",
]


def demo_observed_evidence() -> list[EvidenceItem]:
    return [
        EvidenceItem(category=EvidenceCategory.OBSERVED, text=text, source=DEMO_EVIDENCE_SOURCE)
        for text in DEMO_OBSERVED_TEXTS
    ]
