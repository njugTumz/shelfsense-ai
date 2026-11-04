"""Keyless tests: browser step interpretation, applicability, failure mapping.

Run from backend/: python -m pytest tests/test_browser.py
Real browser execution is proven by the extended live tests
(test_build_live.py, test_verify_live.py).
"""

from app.models.verification import (
    BrowserStep,
    BrowserStepStatus,
    BrowserTestResult,
    CheckStatus,
    summarize_e2b,
)
from app.services.browser_workflow import (
    BROWSER_ASSERT_SOURCE,
    BROWSER_SCRIPT,
    browser_steps_to_checks,
    determine_required_steps,
    unavailable_browser_checks,
)

DEMO_CRITERIA = [
    "Orders are displayed with customer and items.",
    "Order details can be viewed.",
    "Status can be changed forward one step (Received -> Preparing -> Ready -> Completed).",
    "Invalid status changes are rejected with a visible message.",
    "Status changes update the visible interface and overview counts.",
    "The application builds successfully and the running preview can be opened.",
]

BUILD_CRITERIA = [
    "The application builds successfully and the preview serves.",
    "Counter staff can create an order and it appears in the list.",
    "Staff can move an order forward and the list updates.",
    "The board remains visible after page reload.",
]


def _report(*statuses: str) -> BrowserTestResult:
    names = ("load", "create", "advance", "invalid", "persist", "console", "responsive")
    return BrowserTestResult(
        available=True,
        browser="firefox test",
        steps=[
            BrowserStep(name=name, status=BrowserStepStatus(status), detail=f"{name} detail")
            for name, status in zip(names, statuses)
        ],
    )


def test_browser_script_compiles_and_fills_all_fields() -> None:
    compile(BROWSER_SCRIPT, "browser_workflow.check.py", "exec")
    assert "def fill_create_form" in BROWSER_SCRIPT
    assert "spinbutton" in BROWSER_SCRIPT
    assert "create_failed" in BROWSER_SCRIPT


def test_applicability_from_criteria() -> None:
    demo = determine_required_steps(DEMO_CRITERIA)
    assert demo == {"load": True, "create": False, "advance": True, "invalid": True, "persist": False,
                    "console": False, "responsive": False}
    build = determine_required_steps(BUILD_CRITERIA)
    assert build == {"load": True, "create": True, "advance": True, "invalid": False, "persist": True,
                     "console": False, "responsive": False}
    assert determine_required_steps([]) == {
        "load": True, "create": False, "advance": False, "invalid": False, "persist": False,
        "console": False, "responsive": False,
    }
    assert determine_required_steps(None)["load"] is True
    # Quality signals never gate the verdict, whatever the criteria demand.
    assert determine_required_steps(BUILD_CRITERIA)["console"] is False
    assert determine_required_steps(BUILD_CRITERIA)["responsive"] is False


def test_all_pass_maps_to_pass() -> None:
    required = determine_required_steps(BUILD_CRITERIA)
    checks = {c.name: c for c in browser_steps_to_checks(_report(*["pass"] * 7), required)}
    assert checks["browser_loads_app"].status == CheckStatus.PASS
    assert checks["browser_creates_order"].status == CheckStatus.PASS
    assert checks["browser_creates_order"].required is True
    assert checks["browser_advances_status"].status == CheckStatus.PASS
    assert checks["browser_persists_after_reload"].status == CheckStatus.PASS
    assert all("Real browser" in c.detail for c in checks.values())


def test_skip_never_counts_as_pass() -> None:
    required = determine_required_steps(DEMO_CRITERIA)
    report = _report("pass", "not_applicable", "pass", "pass", "fail", "pass", "pass")
    checks = {c.name: c for c in browser_steps_to_checks(report, required)}
    assert checks["browser_creates_order"].status == CheckStatus.SKIPPED
    assert checks["browser_creates_order"].required is False
    # Executed and failed, but not demanded: FAIL status, still non-gating.
    assert checks["browser_persists_after_reload"].status == CheckStatus.FAIL
    assert checks["browser_persists_after_reload"].required is False
    assert summarize_e2b(list(checks.values())) is True


def test_required_missing_workflow_fails() -> None:
    required = determine_required_steps(BUILD_CRITERIA)
    checks = {c.name: c for c in browser_steps_to_checks(
        _report("pass", "not_applicable", "pass", "not_applicable", "pass", "pass", "pass"), required)}
    assert checks["browser_creates_order"].status == CheckStatus.FAIL
    assert checks["browser_creates_order"].required is True
    assert "missing" in checks["browser_creates_order"].detail.lower()
    assert summarize_e2b(list(checks.values())) is False


def test_required_failure_and_blocked_fail_verdict() -> None:
    required = determine_required_steps(BUILD_CRITERIA)
    checks = browser_steps_to_checks(_report("pass", "pass", "fail", "not_applicable", "pass", "pass", "pass"), required)
    by_name = {c.name: c for c in checks}
    assert by_name["browser_advances_status"].status == CheckStatus.FAIL
    assert summarize_e2b(checks) is False

    blocked = browser_steps_to_checks(_report("fail", "blocked", "blocked", "blocked", "blocked", "blocked", "blocked"), required)
    by_blocked = {c.name: c for c in blocked}
    assert by_blocked["browser_loads_app"].status == CheckStatus.FAIL
    assert by_blocked["browser_creates_order"].status == CheckStatus.FAIL
    assert by_blocked["browser_rejects_invalid"].status == CheckStatus.SKIPPED
    assert summarize_e2b(blocked) is False


def test_untestable_invalid_path_never_fails() -> None:
    required = determine_required_steps(DEMO_CRITERIA)
    assert required["invalid"] is True
    checks = {c.name: c for c in browser_steps_to_checks(
        _report("pass", "not_applicable", "pass", "not_applicable", "fail", "pass", "pass"), required)}
    assert checks["browser_rejects_invalid"].status == CheckStatus.SKIPPED
    assert checks["browser_rejects_invalid"].required is False


def test_missing_step_is_fail() -> None:
    required = determine_required_steps(BUILD_CRITERIA)
    report = BrowserTestResult(available=True, steps=[
        BrowserStep(name="load", status=BrowserStepStatus.PASS),
    ])
    checks = {c.name: c for c in browser_steps_to_checks(report, required)}
    assert checks["browser_creates_order"].status == CheckStatus.FAIL
    assert "missing from report" in checks["browser_creates_order"].detail


def test_quality_steps_map_to_non_gating_checks() -> None:
    from app.models.verification import CheckCategory

    required = determine_required_steps(BUILD_CRITERIA)
    checks = {c.name: c for c in browser_steps_to_checks(_report(*["pass"] * 7), required)}
    for name in ("browser_console_clean", "browser_responsive_layout"):
        assert checks[name].status == CheckStatus.PASS
        assert checks[name].required is False
        assert checks[name].category == CheckCategory.QUALITY

    failed = {c.name: c for c in browser_steps_to_checks(
        _report("pass", "pass", "pass", "not_applicable", "pass", "fail", "fail"), required)}
    assert failed["browser_console_clean"].status == CheckStatus.FAIL
    assert failed["browser_console_clean"].required is False
    assert failed["browser_responsive_layout"].status == CheckStatus.FAIL
    assert failed["browser_responsive_layout"].required is False
    assert "Real browser" in failed["browser_console_clean"].detail
    assert summarize_e2b(list(failed.values())) is True


def test_browser_script_captures_console_and_viewport() -> None:
    compile(BROWSER_SCRIPT, "browser_workflow.check.py", "exec")
    assert 'page.on("console"' in BROWSER_SCRIPT
    assert 'page.on("pageerror"' in BROWSER_SCRIPT
    assert "set_viewport_size" in BROWSER_SCRIPT
    assert "scrollWidth" in BROWSER_SCRIPT
    assert '"mobile"' in BROWSER_SCRIPT
    assert '"console"' in BROWSER_SCRIPT
    assert '"responsive"' in BROWSER_SCRIPT
    assert "set_default_timeout(60000)" in BROWSER_SCRIPT
    assert "def fresh_load" in BROWSER_SCRIPT
    assert "firefox_user_prefs" in BROWSER_SCRIPT


def test_unavailable_browser_marks_required_unavailable() -> None:
    required = determine_required_steps(BUILD_CRITERIA)
    checks = unavailable_browser_checks(required, "setup exploded")
    by_name = {c.name: c for c in checks}
    assert all(c.status == CheckStatus.UNAVAILABLE for c in checks)
    assert by_name["browser_loads_app"].required is True
    assert by_name["browser_rejects_invalid"].required is False
    assert summarize_e2b(checks) is False


def test_coverage_uses_browser_evidence() -> None:
    from app.models.interview import StructuredRequirements
    from app.models.verification import CheckCategory, E2BVerification, VerificationCheck
    from app.services.coverage import evaluate_acceptance_criteria

    spec = StructuredRequirements.model_validate({
        "business_context": "c", "problem_statement": "p",
        "objectives": [], "user_roles": [{"name": "r", "description": "d"}],
        "functional_requirements": [{"id": "FR-1", "requirement": "r"}],
        "non_functional_requirements": [], "user_journeys": [],
        "core_features": [], "data_entities": [],
        "acceptance_criteria": BUILD_CRITERIA,
        "assumptions": [], "unresolved_questions": [],
    })
    e2b = E2BVerification(
        available=True, passed=True,
        checks=[
            VerificationCheck(name="production_build", category=CheckCategory.BUILD, status=CheckStatus.PASS),
            VerificationCheck(name="preview_serves", category=CheckCategory.RUNTIME, status=CheckStatus.PASS),
            VerificationCheck(name="browser_creates_order", category=CheckCategory.FUNCTIONAL, status=CheckStatus.PASS),
            VerificationCheck(name="browser_advances_status", category=CheckCategory.FUNCTIONAL, status=CheckStatus.PASS),
            VerificationCheck(name="browser_persists_after_reload", category=CheckCategory.FUNCTIONAL, status=CheckStatus.PASS),
        ],
    )
    mappings = {m.criterion: m for m in evaluate_acceptance_criteria(spec, e2b)}
    assert all(m.status.value == "supported" for m in mappings.values())
    assert "browser_creates_order" in mappings[BUILD_CRITERIA[1]].by_checks
    assert "browser_persists_after_reload" in mappings[BUILD_CRITERIA[3]].by_checks


def _assert_helpers():
    """The exact assertion logic shipped to E2B (single source of truth)."""
    namespace: dict = {}
    exec(BROWSER_ASSERT_SOURCE, namespace)
    return namespace


def test_assert_source_compiles_and_is_used_by_script() -> None:
    compile(BROWSER_ASSERT_SOURCE, "browser_assert.py", "exec")
    compile(BROWSER_SCRIPT, "browser_workflow.check.py", "exec")
    assert "from browser_assert import" in BROWSER_SCRIPT
    for helper in (
        "parse_button_target",
        "display_text_without_buttons",
        "status_shown_in_display",
        "button_moved_past",
    ):
        assert helper in BROWSER_SCRIPT


def test_exact_status_badge_counts_as_shown() -> None:
    helpers = _assert_helpers()
    display = helpers["display_text_without_buttons"](
        "BROWSER-1\nTicket #1\nPreparing\nAdvance to Ready", ["Advance to Ready"])
    assert helpers["status_shown_in_display"](display, "Preparing") is True


def test_prefixed_status_text_counts_as_shown() -> None:
    """Regression: 'Status: Preparing' rendering (mission f30ec35cdc49) must pass."""
    helpers = _assert_helpers()
    display = helpers["display_text_without_buttons"](
        "ID order-1 Ticket #1 BROWSER-1 Status: Preparing Advance to Ready",
        ["Advance to Ready"],
    )
    assert helpers["status_shown_in_display"](display, "Preparing") is True
    assert helpers["status_shown_in_display"](display, "Ready") is False


def test_control_wording_cannot_fake_status_display() -> None:
    """A placebo button naming the target must not satisfy the display check."""
    helpers = _assert_helpers()
    display = helpers["display_text_without_buttons"](
        "ID order-1 Ticket #1 BROWSER-1 Status: Received Advance to Preparing",
        ["Advance to Preparing"],
    )
    assert helpers["status_shown_in_display"](display, "Preparing") is False
    assert helpers["status_shown_in_display"](display, "Received") is True


def test_status_match_uses_whole_words() -> None:
    helpers = _assert_helpers()
    shown = helpers["status_shown_in_display"]
    assert shown("Already queued", "Ready") is False
    assert shown("Status: preparing", "Preparing") is False
    assert shown("Ready - Pickup Shelf (1)", "Ready") is True
    assert shown("", "Received") is False
    assert shown("Status: Received", "") is False


def test_button_target_parsing() -> None:
    parse = _assert_helpers()["parse_button_target"]
    assert parse("Advance to Preparing") == "Preparing"
    assert parse("Mark Ready") == "Ready"
    assert parse("Next") is None
    assert parse("") is None


def test_button_moved_past_reached_status() -> None:
    moved_past = _assert_helpers()["button_moved_past"]
    assert moved_past(["Advance to Ready"], "Preparing") is True
    assert moved_past(["Advance to Preparing"], "Preparing") is False
    assert moved_past([], "Completed") is True  # terminal: no control left
    assert moved_past(["Advance to Completed", "Advance to Ready"], "Ready") is False
