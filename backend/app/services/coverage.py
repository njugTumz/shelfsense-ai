"""Coverage mapping: approved spec <-> verification evidence (pure).

Two honest mappings, both keyless-testable:
- feature coverage: each functional requirement is IMPLEMENTED (model
  claim + source trace, both static), UNIMPLEMENTED (model disclosed),
  or MISSING_EVIDENCE (claimed but untraceable, or never claimed).
- acceptance mapping: each criterion is SUPPORTED (the check
  exercising its distinguishing path passed), PARTIAL (only family
  evidence passed), or UNVERIFIED. Static evidence never marks
  SUPPORTED.
"""

from __future__ import annotations

import re

from app.models.generation import (
    AcceptanceMapping,
    AcceptanceSupport,
    CoverageReport,
    GenerationOutput,
    RequirementCoverage,
    RequirementCoverageStatus,
)
from app.models.interview import StructuredRequirements
from app.models.verification import CheckStatus, E2BVerification
from app.services.architecture import classify_architecture
from app.services.browser_workflow import NEGATED_RELOAD_REFRESH

_STOPWORDS = frozenset({
    "allow", "allows", "display", "displays", "show", "shows", "shown",
    "using", "with", "from", "into", "that", "this", "will", "each",
    "such", "user", "users", "page", "pages", "data", "item", "items",
    "list", "lists", "view", "views", "order", "orders",
})

#: criterion keyword -> E2B checks that genuinely support it. A criterion
#: is SUPPORTED when ANY mapped check passed (browser execution and the
#: node logic test are alternative evidence, not joint requirements).
_CRITERION_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("build", "compile", "typescript", "typecheck"), ("production_build",)),
    (("install", "dependencies"), ("npm_install",)),
    (
        ("preview", "opens", "served", "launches", "loads in the browser"),
        ("preview_serves", "browser_loads_app"),
    ),
    (("asset", "stylesheet", "bundle"), ("assets_resolve",)),
    (("create", "add", "new order", "entry", "submit"), ("browser_creates_order",)),
    (
        ("display", "shown", "visible", "appear", "list"),
        ("browser_creates_order", "browser_advances_status"),
    ),
    (
        ("status", "transition", "preparing", "ready", "move", "advance", "update"),
        (
            "browser_advances_status",
            "status_transitions_execute",
            "browser_walks_full_lifecycle",
        ),
    ),
    (("invalid", "reject"), ("browser_rejects_invalid",)),
    (
        ("persist", "reload", "refresh", "storage", "survive"),
        ("browser_persists_after_reload",),
    ),
)


def _words(text: str) -> set[str]:
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return {w for w in re.findall(r"[a-z]{4,}", split.lower()) if w not in _STOPWORDS}


def evaluate_feature_coverage(
    spec: StructuredRequirements, output: GenerationOutput
) -> list[RequirementCoverage]:
    source = "\n".join(f.content for f in output.files)
    source_words = _words(source)
    generated_paths = [f.path for f in output.files]
    disclosed = {item.requirement_id: item.reason for item in output.unimplemented}
    coverages: list[RequirementCoverage] = []
    for req in spec.functional_requirements:
        if req.id in disclosed:
            coverages.append(RequirementCoverage(
                requirement_id=req.id,
                requirement=req.requirement,
                status=RequirementCoverageStatus.UNIMPLEMENTED,
                detail=f"Model disclosed as unimplemented: {disclosed[req.id] or 'no reason given'}",
            ))
            continue
        claimed = [m for m in output.feature_map if req.id in m.requirement_ids]
        if not claimed:
            coverages.append(RequirementCoverage(
                requirement_id=req.id,
                requirement=req.requirement,
                status=RequirementCoverageStatus.MISSING_EVIDENCE,
                detail="No generated feature claims this requirement.",
            ))
            continue
        matched = sorted({w for m in claimed for w in _words(m.feature)} & source_words)
        if matched:
            coverages.append(RequirementCoverage(
                requirement_id=req.id,
                requirement=req.requirement,
                status=RequirementCoverageStatus.IMPLEMENTED,
                files=list(generated_paths),
                detail=f"Claimed by feature_map; source trace (static): {', '.join(matched[:8])}",
            ))
        else:
            coverages.append(RequirementCoverage(
                requirement_id=req.id,
                requirement=req.requirement,
                status=RequirementCoverageStatus.MISSING_EVIDENCE,
                files=list(generated_paths),
                detail="Claimed by feature_map but no source trace found (static check).",
            ))
    return coverages


#: Lifecycle statuses a criterion can name as traversal targets.
_LIFECYCLE_STATUSES = ("Received", "Preparing", "Ready", "Served", "Completed", "Cancelled")

#: Statuses the two-hop advance check traverses by contract; anything
#: beyond needs a walker chain covering it.
ADVANCE_COVERED_STATUSES = ("Received", "Preparing", "Ready")

#: Checks executing status transitions (chains consulted for range).
_TRANSITION_CHECKS = (
    "browser_advances_status",
    "status_transitions_execute",
    "browser_walks_full_lifecycle",
)

_TRANSITION_KEYWORDS = ("status", "transition", "preparing", "ready", "move", "advance", "update")

#: Distinguishing path per criterion intent: keywords -> the check
#: exercising it, or None when no executed check covers that intent.
#: Persist honors the required-step negation rule (a "without reload"
#: demand is not persistence evidence). "Served" is deliberately not a
#: preview keyword: it names a lifecycle status, not the served bundle.
_DISTINGUISHING_INTENTS: tuple[tuple[tuple[str, ...], str | None], ...] = (
    (("invalid", "reject"), "browser_rejects_invalid"),
    (("persist", "reload", "refresh", "storage", "survive"), "browser_persists_after_reload"),
    (("edit",), None),
    (("cancel",), None),
    (("paid", "payment"), None),
    (("dashboard",), None),
    (("filter",), None),
    (("arrival order", "oldest"), None),
    (("create", "add", "new order", "entry", "submit"), "browser_creates_order"),
    (("preview", "opens", "launches", "loads in the browser"), "preview_serves"),
    (("build", "compile", "typescript", "typecheck"), "production_build"),
    (("install", "dependencies"), "npm_install"),
    (("asset", "stylesheet", "bundle"), "assets_resolve"),
)


def criterion_statuses(criterion: str) -> list[str]:
    """Lifecycle statuses a criterion names, in mention order.

    Exact word match (case-insensitive): "already" must not read as
    "Ready". Paid/cancelled states are demand intents, not traversal
    targets, so they are intentionally not listed.
    """
    found: list[str] = []
    for match in re.finditer(r"[A-Za-z]+", criterion or ""):
        word = match.group(0).lower()
        for status in _LIFECYCLE_STATUSES:
            if word == status.lower() and status not in found:
                found.append(status)
    return found


def _transition_met(
    statuses: list[str], passed: set[str], chains: dict[str, list[str]]
) -> tuple[bool, list[str]]:
    """Whether executed evidence traverses the named statuses.

    The two-hop advance (and the static transition test) cover
    Received->Preparing->Ready by contract; anything beyond needs a
    passed transition check whose structured chain covers it.
    """
    if not statuses:
        ok = any(c in passed for c in _TRANSITION_CHECKS)
        return ok, [] if ok else ["no transition check passed"]
    covered: set[str] = set()
    if any(c in passed for c in ("browser_advances_status", "status_transitions_execute")):
        covered.update(s for s in statuses if s in ADVANCE_COVERED_STATUSES)
    for name in passed:
        if name in _TRANSITION_CHECKS:
            covered.update(s for s in statuses if s in (chains.get(name) or ()))
    missing = [s for s in statuses if s not in covered]
    return (not missing), missing


def _distinguishing_gaps(
    criterion: str, passed: set[str], chains: dict[str, list[str]]
) -> list[str]:
    """Reasons the distinguishing path is unproven (empty == proven)."""
    lowered = criterion.lower()
    plain_text = NEGATED_RELOAD_REFRESH.sub(" ", lowered)
    gaps: list[str] = []
    for keywords, check in _DISTINGUISHING_INTENTS:
        haystack = plain_text if check == "browser_persists_after_reload" else lowered
        if not any(kw in haystack for kw in keywords):
            continue
        if check is None:
            gaps.append(f"no check covers the '{keywords[0]}' path")
        elif check not in passed:
            gaps.append(f"{check} did not pass")
    statuses = criterion_statuses(criterion)
    if statuses or any(kw in lowered for kw in _TRANSITION_KEYWORDS):
        ok, missing = _transition_met(statuses, passed, chains)
        if not ok:
            if missing == ["no transition check passed"]:
                gaps.append("no transition check passed")
            else:
                gaps.append(f"statuses {missing} beyond traversed evidence")
    return gaps


def evaluate_acceptance_criteria(
    spec: StructuredRequirements, e2b: E2BVerification
) -> list[AcceptanceMapping]:
    """Map each criterion to executed E2B evidence.

    SUPPORTED: the check exercising the distinguishing path passed.
    PARTIAL: only family evidence passed. UNVERIFIED: nothing mapped
    passed. Static evidence never marks SUPPORTED.
    """
    passed = {c.name for c in e2b.checks if c.status == CheckStatus.PASS} if e2b.available else set()
    chains = {c.name: list(c.chain or []) for c in e2b.checks}
    mappings: list[AcceptanceMapping] = []
    for criterion in spec.acceptance_criteria:
        lowered = criterion.lower()
        supporting = sorted({check for keywords, checks in _CRITERION_RULES
                             for check in checks if any(k in lowered for k in keywords)})
        passed_support = [check for check in supporting if check in passed]
        if not passed_support:
            if supporting:
                detail = f"Needs {', '.join(supporting)}: no mapped check passed"
            else:
                detail = "No executed check covers this (static/DOM testing unavailable)"
            mappings.append(AcceptanceMapping(
                criterion=criterion,
                status=AcceptanceSupport.UNVERIFIED,
                detail=detail,
            ))
            continue
        gaps = _distinguishing_gaps(criterion, passed, chains)
        if not gaps:
            mappings.append(AcceptanceMapping(
                criterion=criterion,
                status=AcceptanceSupport.SUPPORTED,
                by_checks=passed_support,
                detail=f"Executed checks passed: {', '.join(passed_support)}",
            ))
        else:
            mappings.append(AcceptanceMapping(
                criterion=criterion,
                status=AcceptanceSupport.PARTIAL,
                by_checks=passed_support,
                detail=(f"Partially covered by {', '.join(passed_support)}; "
                        f"distinguishing path not executed: {'; '.join(sorted(gaps))}"),
            ))
    return mappings


def build_coverage_report(
    spec: StructuredRequirements, output: GenerationOutput, e2b: E2BVerification
) -> CoverageReport:
    return CoverageReport(
        requirements=evaluate_feature_coverage(spec, output),
        acceptance=evaluate_acceptance_criteria(spec, e2b),
        architecture=classify_architecture(spec, output),
    )
