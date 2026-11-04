"""Coverage mapping: approved spec <-> verification evidence (pure).

Two honest mappings, both keyless-testable:
- feature coverage: each functional requirement is IMPLEMENTED (model
  claim + source trace, both static), UNIMPLEMENTED (model disclosed),
  or MISSING_EVIDENCE (claimed but untraceable, or never claimed).
- acceptance mapping: each criterion is SUPPORTED (named executed E2B
  checks passed) or UNVERIFIED. Static evidence never marks SUPPORTED.
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
        ("browser_advances_status", "status_transitions_execute"),
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


def evaluate_acceptance_criteria(
    spec: StructuredRequirements, e2b: E2BVerification
) -> list[AcceptanceMapping]:
    passed = {c.name for c in e2b.checks if c.status == CheckStatus.PASS} if e2b.available else set()
    mappings: list[AcceptanceMapping] = []
    for criterion in spec.acceptance_criteria:
        lowered = criterion.lower()
        supporting = sorted({check for keywords, checks in _CRITERION_RULES
                             for check in checks if any(k in lowered for k in keywords)})
        passed_support = [check for check in supporting if check in passed]
        if passed_support:
            mappings.append(AcceptanceMapping(
                criterion=criterion,
                status=AcceptanceSupport.SUPPORTED,
                by_checks=passed_support,
                detail=f"Executed checks passed: {', '.join(passed_support)}",
            ))
        elif supporting:
            mappings.append(AcceptanceMapping(
                criterion=criterion,
                status=AcceptanceSupport.UNVERIFIED,
                detail=f"Needs {', '.join(supporting)}: no mapped check passed",
            ))
        else:
            mappings.append(AcceptanceMapping(
                criterion=criterion,
                status=AcceptanceSupport.UNVERIFIED,
                detail="No executed check covers this (static/DOM testing unavailable)",
            ))
    return mappings


def build_coverage_report(
    spec: StructuredRequirements, output: GenerationOutput, e2b: E2BVerification
) -> CoverageReport:
    return CoverageReport(
        requirements=evaluate_feature_coverage(spec, output),
        acceptance=evaluate_acceptance_criteria(spec, e2b),
    )
