"""Keyless tests: browser step interpretation, applicability, failure mapping.

Run from backend/: python -m pytest tests/test_browser.py
Real browser execution is proven by the extended live tests
(test_build_live.py, test_verify_live.py).
"""

import pytest

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
    names = ("load", "create", "advance", "invalid", "persist", "console", "responsive", "storage", "walk")
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
                    "console": False, "responsive": False, "storage": False, "walk": True}
    build = determine_required_steps(BUILD_CRITERIA)
    assert build == {"load": True, "create": True, "advance": True, "invalid": False, "persist": True,
                     "console": False, "responsive": False, "storage": False, "walk": True}
    assert determine_required_steps([]) == {
        "load": True, "create": False, "advance": False, "invalid": False, "persist": False,
        "console": False, "responsive": False, "storage": False, "walk": False,
    }
    assert determine_required_steps(None)["load"] is True
    # Quality signals never gate the verdict, whatever the criteria demand.
    assert determine_required_steps(BUILD_CRITERIA)["console"] is False
    assert determine_required_steps(BUILD_CRITERIA)["responsive"] is False


def test_all_pass_maps_to_pass() -> None:
    required = determine_required_steps(BUILD_CRITERIA)
    checks = {c.name: c for c in browser_steps_to_checks(_report(*["pass"] * 9), required)}
    assert checks["browser_loads_app"].status == CheckStatus.PASS
    assert checks["browser_creates_order"].status == CheckStatus.PASS
    assert checks["browser_creates_order"].required is True
    assert checks["browser_advances_status"].status == CheckStatus.PASS
    assert checks["browser_persists_after_reload"].status == CheckStatus.PASS
    assert checks["browser_walks_full_lifecycle"].status == CheckStatus.PASS
    assert checks["browser_walks_full_lifecycle"].required is True
    assert all("Real browser" in c.detail for c in checks.values())


def test_skip_never_counts_as_pass() -> None:
    required = determine_required_steps(DEMO_CRITERIA)
    report = _report("pass", "not_applicable", "pass", "pass", "fail", "pass", "pass", "pass", "pass")
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
        _report("pass", "not_applicable", "pass", "not_applicable", "pass", "pass", "pass", "pass", "pass"), required)}
    assert checks["browser_creates_order"].status == CheckStatus.FAIL
    assert checks["browser_creates_order"].required is True
    assert "missing" in checks["browser_creates_order"].detail.lower()
    assert summarize_e2b(list(checks.values())) is False


def test_required_failure_and_blocked_fail_verdict() -> None:
    required = determine_required_steps(BUILD_CRITERIA)
    checks = browser_steps_to_checks(_report("pass", "pass", "fail", "not_applicable", "pass", "pass", "pass", "pass", "pass"), required)
    by_name = {c.name: c for c in checks}
    assert by_name["browser_advances_status"].status == CheckStatus.FAIL
    assert summarize_e2b(checks) is False

    blocked = browser_steps_to_checks(_report("fail", "blocked", "blocked", "blocked", "blocked", "blocked", "blocked", "blocked", "blocked"), required)
    by_blocked = {c.name: c for c in blocked}
    assert by_blocked["browser_loads_app"].status == CheckStatus.FAIL
    assert by_blocked["browser_creates_order"].status == CheckStatus.FAIL
    assert by_blocked["browser_rejects_invalid"].status == CheckStatus.SKIPPED
    assert by_blocked["browser_walks_full_lifecycle"].status == CheckStatus.FAIL
    assert summarize_e2b(blocked) is False


def test_untestable_invalid_path_never_fails() -> None:
    required = determine_required_steps(DEMO_CRITERIA)
    assert required["invalid"] is True
    checks = {c.name: c for c in browser_steps_to_checks(
        _report("pass", "not_applicable", "pass", "not_applicable", "fail", "pass", "pass", "pass", "pass"), required)}
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
    checks = {c.name: c for c in browser_steps_to_checks(_report(*["pass"] * 9), required)}
    for name in ("browser_console_clean", "browser_responsive_layout"):
        assert checks[name].status == CheckStatus.PASS
        assert checks[name].required is False
        assert checks[name].category == CheckCategory.QUALITY

    failed = {c.name: c for c in browser_steps_to_checks(
        _report("pass", "pass", "pass", "not_applicable", "pass", "fail", "fail", "pass", "pass"), required)}
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


def _acceptance_spec(criteria):
    from app.models.interview import StructuredRequirements

    return StructuredRequirements.model_validate({
        "business_context": "c", "problem_statement": "p",
        "objectives": [], "user_roles": [{"name": "r", "description": "d"}],
        "functional_requirements": [{"id": "FR-1", "requirement": "r"}],
        "non_functional_requirements": [], "user_journeys": [],
        "core_features": [], "data_entities": [],
        "acceptance_criteria": list(criteria),
        "assumptions": [], "unresolved_questions": [],
    })


def _passing_e2b(names, chains=None):
    from app.models.verification import CheckCategory, E2BVerification, VerificationCheck

    chains = chains or {}
    return E2BVerification(
        available=True, passed=True,
        checks=[
            VerificationCheck(
                name=name, category=CheckCategory.FUNCTIONAL, status=CheckStatus.PASS,
                chain=list(chains.get(name, ())),
            )
            for name in names
        ],
    )


def test_criterion_statuses_fixture_matrix() -> None:
    from app.services.coverage import criterion_statuses

    assert criterion_statuses("Move from Received to Preparing") == ["Received", "Preparing"]
    assert criterion_statuses("READY then served then completed") == ["Ready", "Served", "Completed"]
    assert criterion_statuses("Ready, ready, READY.") == ["Ready"]
    assert criterion_statuses("Orders already served stay put.") == ["Served"]
    assert criterion_statuses("Mark the order as paid.") == []
    assert criterion_statuses("Cancel the entire order.") == []
    assert criterion_statuses("") == []
    assert criterion_statuses(None) == []


def test_distinguishing_supported_vs_partial_vs_unverified() -> None:
    from app.services.coverage import evaluate_acceptance_criteria

    full = _passing_e2b([
        "production_build", "preview_serves", "browser_loads_app",
        "browser_creates_order", "browser_advances_status",
        "browser_persists_after_reload", "browser_rejects_invalid",
    ])
    supported = [
        "Counter staff can create an order and it appears in the list.",
        "Staff can move an order from Received to Preparing.",
        "Staff can move an order from Preparing to Ready.",
        "The board remains visible after page reload.",
        "Invalid status changes are rejected with a visible message.",
        "The application builds successfully and the preview serves.",
    ]
    for text in supported:
        (mapping,) = evaluate_acceptance_criteria(_acceptance_spec([text]), full)
        assert mapping.status.value == "supported", text
    partial = [
        "Staff can move an order from Ready to Served.",
        "Cashier can mark a Served order as paid and it moves to Completed.",
        "Waiter can edit an existing sent order and the updated items are visible.",
        "Waiter can cancel an entire order after sending and the list updates counts.",
        "Manager dashboard shows outstanding count and daily paid total and updates on change.",
        "Kitchen lists orders in arrival order oldest first.",
    ]
    for text in partial:
        (mapping,) = evaluate_acceptance_criteria(_acceptance_spec([text]), full)
        assert mapping.status.value == "partial", text
        assert mapping.by_checks, text
    (mapping,) = evaluate_acceptance_criteria(
        _acceptance_spec(["Staff can move an order from Received to Preparing."]),
        _passing_e2b(["production_build"]),
    )
    assert mapping.status.value == "unverified"


def test_walker_chain_covers_beyond_advance_statuses() -> None:
    from app.services.coverage import evaluate_acceptance_criteria

    text = "Staff can move an order from Ready to Served."
    short = _passing_e2b(
        ["browser_advances_status", "browser_walks_full_lifecycle"],
        chains={"browser_walks_full_lifecycle": ["Received", "Preparing", "Ready"]},
    )
    (mapping,) = evaluate_acceptance_criteria(_acceptance_spec([text]), short)
    assert mapping.status.value == "partial"
    assert "Served" in mapping.detail
    full = _passing_e2b(
        ["browser_advances_status", "browser_walks_full_lifecycle"],
        chains={"browser_walks_full_lifecycle": ["Received", "Preparing", "Ready", "Served"]},
    )
    (mapping,) = evaluate_acceptance_criteria(_acceptance_spec([text]), full)
    assert mapping.status.value == "supported"


C46D_CRITERIA = [
    "Waiter selects a labeled table, adds items and submits, and a new order appears in Received with that table label",
    "Staff can move an order from Received to Preparing and the list/board updates its status immediately",
    "Staff can move an order from Preparing to Ready and the kitchen queue and central list both reflect Ready",
    "Staff can move an order from Ready to Served and the order leaves the kitchen Ready queue",
    "Cashier can mark a Served table order as paid and it moves to Completed and is removed from the outstanding/open list",
    "Manager dashboard shows outstanding order count and daily paid total and both update immediately when a payment is marked",
    "Waiter can edit an existing sent order to add or change an item and the updated items are visible in kitchen/central views",
    "Waiter can cancel an item or entire order after sending and the UI shows cancelled/removed state and updates counts",
    "Kitchen view lists only Received and Preparing orders in arrival order and updates without page reload when new orders arrive",
]

C46D_PASSED_CHECKS = [
    "assets_resolve", "browser_advances_status", "browser_console_clean",
    "browser_creates_order", "browser_loads_app", "browser_persists_after_reload",
    "browser_responsive_layout", "browser_storage_holds_status", "files_uploaded",
    "node_runtime", "npm_install", "preview_serves", "production_build", "sandbox_create",
]


def test_c46d_acceptance_golden_mapping() -> None:
    """Golden regression: the c46d live run's criteria map to 3 SUPPORTED
    / 6 PARTIAL under distinguishing-path rules (sanitized inputs: plain
    criterion texts plus stored passed check names, no tokens or URLs)."""
    from app.services.coverage import evaluate_acceptance_criteria

    mappings = evaluate_acceptance_criteria(
        _acceptance_spec(C46D_CRITERIA), _passing_e2b(C46D_PASSED_CHECKS))
    assert [m.status.value for m in mappings] == [
        "supported", "supported", "supported",
        "partial", "partial", "partial", "partial", "partial", "partial",
    ]
    assert "Served" in mappings[3].detail
    assert "'edit' path" in mappings[6].detail
    assert "'cancel' path" in mappings[7].detail
    assert "'arrival order' path" in mappings[8].detail


def test_browser_step_chain_flows_to_check() -> None:
    report = BrowserTestResult(
        available=True, browser="firefox test",
        steps=[
            BrowserStep(name="load", status="pass", chain=[]),
            BrowserStep(
                name="advance", status="pass", detail="advanced it",
                chain=["Received", "Preparing", "Ready"],
            ),
        ],
    )
    checks = {c.name: c for c in browser_steps_to_checks(report, {"advance": True})}
    assert checks["browser_advances_status"].chain == ["Received", "Preparing", "Ready"]


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


def test_persist_requirement_ignores_negated_reload() -> None:
    """Regression (mission c46d5524859c): 'updates without page reload'
    demands live updates, not reload persistence, and must not arm the
    persist gate."""
    live = determine_required_steps([
        "Kitchen view lists orders and updates without page reload when new orders arrive.",
    ])
    assert live["persist"] is False
    assert live["load"] is True
    assert determine_required_steps(["Updates appear with no reload needed."])["persist"] is False
    assert determine_required_steps(["Board updates without manual refresh."])["persist"] is False
    for text in (
        "Orders persist across reload.",
        "Orders survive refresh.",
        "Orders are kept in browser storage.",
        "After page reload all previously created orders remain visible.",
    ):
        assert determine_required_steps([text])["persist"] is True, text


def test_negation_never_swallows_genuine_demands() -> None:
    """Negation handling stays adjacent: genuine demands mentioning
    reload elsewhere in the sentence keep the gate armed."""
    assert determine_required_steps(
        ["Works without login; reload persistence is required."])["persist"] is True
    assert determine_required_steps(["No orders lost on reload."])["persist"] is True
    assert determine_required_steps(
        ["Persists across reload without manual refresh."])["persist"] is True


def test_order_items_matches_exact_child_text() -> None:
    """Regression (mission c46d5524859c): order lookups must pin to the
    exact marker via whole-text child matching, so a '-2' suffixed
    sibling can never steal the match (substring has_text matched it,
    and a lookahead cannot work because Playwright joins element texts
    without separators)."""

    class _Page:
        def __init__(self) -> None:
            self.calls: dict = {}

        def get_by_text(self, text: str, exact: bool = False) -> str:
            self.calls["text"] = (text, exact)
            return "INNER"

        def locator(self, selector: str, has=None) -> str:
            self.calls["locator"] = (selector, has)
            return "ITEMS"

    page = _Page()
    assert _assert_helpers()["order_items"](page, "BROWSER-1") == "ITEMS"
    assert page.calls["text"] == ("BROWSER-1", True)
    assert page.calls["locator"] == ("li", "INNER")


def test_first_with_select_prefers_rendering_with_control() -> None:
    """Multi-view apps render one order in several lists; select-based
    steps must use the rendering that has the control, whatever the
    DOM order, and report absence instead of timing out."""

    class _Item:
        def __init__(self, selects: int) -> None:
            self._selects = selects

        def locator(self, name: str) -> "_Item":
            assert name == "select"
            return self

        def count(self) -> int:
            return self._selects

    pick = _assert_helpers()["first_with_select"]
    queue_item = _Item(0)
    board_item = _Item(1)
    assert pick([queue_item, board_item]) is board_item
    assert pick([board_item, queue_item]) is board_item
    assert pick([queue_item]) is None
    assert pick([]) is None
    assert pick(None) is None


def test_skip_option_state_matrix() -> None:
    """An illegal skip is attempted only when the UI offers an enabled
    path; disabled/absent options mean the invalid path is not
    expressible (correct restrictive design, not a defect)."""
    state = _assert_helpers()["skip_option_state"]
    assert state([("Received", False), ("Ready", True)], "Ready") == "disabled"
    assert state([("Received", False), ("Ready", False)], "Ready") == "selectable"
    assert state([("Received", False)], "Ready") == "absent"
    assert state([], "Ready") == "absent"
    assert state(None, "Ready") == "absent"
    assert state([("Ready", True), ("Ready", False)], "Ready") == "selectable"


def test_browser_script_uses_exact_order_matching() -> None:
    compile(BROWSER_SCRIPT, "browser_workflow.check.py", "exec")
    compile(BROWSER_ASSERT_SOURCE, "browser_assert.py", "exec")
    for helper in ("order_items", "first_with_select", "skip_option_state"):
        assert helper in BROWSER_ASSERT_SOURCE
        assert helper in BROWSER_SCRIPT
    # No raw substring order matching remains in asserted-identity steps.
    assert "has_text=identifier)" not in BROWSER_SCRIPT
    assert "has_text=target)" not in BROWSER_SCRIPT
    assert "order_items(page, identifier)" in BROWSER_SCRIPT
    assert "order_items(page, target)" in BROWSER_SCRIPT


def _storage_dump(*orders: dict) -> dict:
    import json

    return {"app-orders-v1": json.dumps(list(orders))}


def test_stored_statuses_for_finds_reached_status() -> None:
    find = _assert_helpers()["stored_statuses_for"]
    dump = _storage_dump(
        {"id": "o1", "status": "Ready",
         "items": [{"description": "BROWSER-1", "quantity": 1}]},
        {"id": "o2", "status": "Received",
         "items": [{"description": "BROWSER-1-2", "quantity": 1}]},
    )
    assert find(dump, "BROWSER-1") == ["Ready"]
    assert find(dump, "BROWSER-1-2") == ["Received"]
    assert find(dump, "BROWSER-9") == []


def test_stored_statuses_for_ignores_unstructured_values() -> None:
    find = _assert_helpers()["stored_statuses_for"]
    assert find({"k": "BROWSER-1 at Ready"}, "BROWSER-1") == []
    assert find({"k": "{not json", "j": "[1, 2]"}, "BROWSER-1") == []
    assert find({}, "BROWSER-1") == []
    assert find(None, "BROWSER-1") == []
    assert find({"k": '"BROWSER-1"'}, "BROWSER-1") == [None]
    assert find({"k": "[1, 2]"}, "") == []


def test_storage_check_never_gates_verdict() -> None:
    from app.services.browser_workflow import STEP_CHECK_NAMES

    assert STEP_CHECK_NAMES["storage"] == "browser_storage_holds_status"
    demanding = determine_required_steps([
        "Orders persist across reload.",
        "Invalid status changes are rejected.",
    ])
    assert demanding["persist"] is True
    assert demanding["invalid"] is True
    assert demanding["storage"] is False
    checks = {c.name: c for c in browser_steps_to_checks(
        _report("pass", "pass", "pass", "pass", "pass", "pass", "pass", "fail", "pass"), demanding)}
    assert checks["browser_storage_holds_status"].status == CheckStatus.FAIL
    assert checks["browser_storage_holds_status"].required is False
    assert summarize_e2b(list(checks.values())) is True


def test_browser_script_dumps_storage_after_advance() -> None:
    compile(BROWSER_SCRIPT, "browser_workflow.check.py", "exec")
    assert "stored_statuses_for" in BROWSER_ASSERT_SOURCE
    assert "stored_statuses_for" in BROWSER_SCRIPT
    assert "window.localStorage" in BROWSER_SCRIPT
    assert "window.sessionStorage" in BROWSER_SCRIPT
    assert 'rec("storage", "pass"' in BROWSER_SCRIPT
    assert 'rec("storage", "fail"' in BROWSER_SCRIPT
    assert 'rec("storage", "blocked"' in BROWSER_SCRIPT
    assert 'rec("storage", "not_applicable"' in BROWSER_SCRIPT
    storage_at = BROWSER_SCRIPT.index('# --- storage:')
    assert BROWSER_SCRIPT.index('# --- advance') < storage_at
    assert storage_at < BROWSER_SCRIPT.index('# --- invalid')


_COLLISION_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Collision fixture</title></head>
<body><h1>Urban Bites</h1>
<section aria-label="Kitchen queue"><ul>
<li><p>#L9P9GZ</p><p>{sib}</p><p>Status: Received</p><button>Advance to Preparing</button></li>
</ul></section>
<section aria-label="Central order board"><ul>
<li><p>#L9P9GZ</p><p>{sib}</p><p>Status: Received</p><select><option>Received</option></select></li>
<li><p>#K7Q2AB</p><p>{base}</p><p>Status: Ready</p><select><option>Ready</option></select></li>
</ul></section>
</body></html>"""


def test_order_items_locator_resolves_exact_order_in_real_browser() -> None:
    """End-to-end locator semantics against the c46d failure shape: a
    '-2' suffixed sibling plus select/button-only duplicate renderings
    across two views. The shipped order_items helper must resolve
    exactly the correct li. Skips cleanly when no browser is installed."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    base = "BROWSER-1793839158"
    sib = base + "-2"
    html = _COLLISION_HTML.format(base=base, sib=sib)
    order_items = _assert_helpers()["order_items"]
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            # Fixture validity: old substring logic genuinely collides here.
            assert page.locator("li", has_text=base).count() == 3
            matched = order_items(page, base)
            assert matched.count() == 1
            text = matched.first.inner_text()
            assert base in text and sib not in text
            assert matched.first.locator("select").count() == 1
            assert order_items(page, sib).count() == 2
        finally:
            browser.close()


# --- B2: lifecycle walker ---


def test_walker_template_emits_structured_chain() -> None:
    from app.services.browser_workflow import STEP_CHECK_NAMES

    compile(BROWSER_SCRIPT, "browser_workflow.check.py", "exec")
    assert STEP_CHECK_NAMES["walk"] == "browser_walks_full_lifecycle"
    assert '# --- walk:' in BROWSER_SCRIPT
    assert 'rec("walk", "pass"' in BROWSER_SCRIPT
    assert 'rec("walk", "fail"' in BROWSER_SCRIPT
    assert 'rec("walk", "blocked"' in BROWSER_SCRIPT
    assert "chain=chain" in BROWSER_SCRIPT
    for helper in ("first_with_advance_button", "next_forward_option", "read_display_status"):
        assert helper in BROWSER_ASSERT_SOURCE
        assert helper in BROWSER_SCRIPT
    assert "next_forward_option(opts, current)" in BROWSER_SCRIPT
    assert "verify_hop" in BROWSER_SCRIPT
    assert "range(8)" in BROWSER_SCRIPT
    assert "cycle detected at" in BROWSER_SCRIPT
    assert "hop cap reached" in BROWSER_SCRIPT
    assert "order left all lists" in BROWSER_SCRIPT
    assert "input-gated at" in BROWSER_SCRIPT
    assert "synthetic input filled at" in BROWSER_SCRIPT
    walk_at = BROWSER_SCRIPT.index('# --- walk:')
    assert BROWSER_SCRIPT.index('# --- persist:') < walk_at
    assert walk_at < BROWSER_SCRIPT.index('# --- responsive:')


def test_walk_required_matches_advance_gate() -> None:
    """The walk is transition evidence at full range: required exactly
    when the two-hop advance gate is, across arming and non-arming
    criteria alike."""
    cases = [
        (BUILD_CRITERIA, True),
        (DEMO_CRITERIA, True),
        ([], False),
        (["The application builds successfully."], False),
        (["The board remains visible after page reload."], False),
        (["Counter staff can create an order and it appears in the list."], False),
        (["Invalid changes are rejected with a visible message."], False),
        (["Kitchen view lists only Received and Preparing orders in arrival order."], True),
        (["Status can be changed forward one step."], True),
    ]
    for criteria, expected in cases:
        required = determine_required_steps(criteria)
        assert required["advance"] is expected, criteria
        assert required["walk"] is expected, criteria
    persist_only = determine_required_steps(["The board remains visible after page reload."])
    assert persist_only["persist"] is True
    assert persist_only["walk"] is False
    invalid_only = determine_required_steps(["Invalid changes are rejected with a visible message."])
    assert invalid_only["invalid"] is True
    assert invalid_only["walk"] is False


def test_walk_chain_flows_to_check() -> None:
    from app.models.verification import CheckCategory

    report = BrowserTestResult(
        available=True, browser="firefox test",
        steps=[
            BrowserStep(name="load", status="pass", chain=[]),
            BrowserStep(
                name="walk", status="pass", detail="walked Received -> Preparing",
                chain=["Received", "Preparing"],
            ),
        ],
    )
    checks = {c.name: c for c in browser_steps_to_checks(report, {"walk": True})}
    assert checks["browser_walks_full_lifecycle"].chain == ["Received", "Preparing"]
    assert checks["browser_walks_full_lifecycle"].category == CheckCategory.FUNCTIONAL
    failed = {c.name: c for c in browser_steps_to_checks(BrowserTestResult(
        available=True, browser="firefox test",
        steps=[BrowserStep(
            name="walk", status="fail", detail="hop to Ready not shown",
            chain=["Received", "Preparing"],
        )],
    ), {"walk": True})}
    assert failed["browser_walks_full_lifecycle"].status == CheckStatus.FAIL
    assert failed["browser_walks_full_lifecycle"].chain == ["Received", "Preparing"]


def test_walk_failure_gates_verdict_only_when_required() -> None:
    required = determine_required_steps(BUILD_CRITERIA)
    assert required["walk"] is True
    checks = browser_steps_to_checks(
        _report("pass", "pass", "pass", "not_applicable", "pass", "pass", "pass", "pass", "fail"),
        required,
    )
    by_name = {c.name: c for c in checks}
    assert by_name["browser_walks_full_lifecycle"].status == CheckStatus.FAIL
    assert by_name["browser_walks_full_lifecycle"].required is True
    assert summarize_e2b(checks) is False

    unrequired = determine_required_steps(["The board remains visible after page reload."])
    assert unrequired["walk"] is False
    checks = browser_steps_to_checks(
        _report("pass", "pass", "pass", "not_applicable", "pass", "pass", "pass", "pass", "fail"),
        unrequired,
    )
    by_name = {c.name: c for c in checks}
    assert by_name["browser_walks_full_lifecycle"].status == CheckStatus.FAIL
    assert by_name["browser_walks_full_lifecycle"].required is False
    assert summarize_e2b(checks) is True


def test_next_forward_option_matrix() -> None:
    nxt = _assert_helpers()["next_forward_option"]
    opts = [("Received", False), ("Preparing", False), ("Ready", False), ("Served", False)]
    assert nxt(opts, "Received") == "Preparing"
    assert nxt(opts, "Ready") == "Served"
    assert nxt(opts, "Served") is None
    assert nxt(opts, "Completed") is None
    assert nxt(opts, None) is None
    assert nxt([], "Received") is None
    assert nxt(None, "Received") is None
    skipped = [("Received", False), ("Preparing", True), ("Ready", False)]
    assert nxt(skipped, "Received") == "Ready"
    destructive = [("Received", False), ("Cancelled", False), ("Preparing", False)]
    assert nxt(destructive, "Received") == "Preparing"
    only_cancel = [("Received", False), ("Cancelled", False)]
    assert nxt(only_cancel, "Received") is None
    duplicate = [("Preparing", False), ("Preparing", False)]
    assert nxt(duplicate, "Preparing") == "Preparing"


def test_read_display_status_matrix() -> None:
    read = _assert_helpers()["read_display_status"]
    assert read("WALK-1 Status: Preparing Advance to Ready") == "Preparing"
    assert read("ID x Status:Ready") == "Ready"
    assert read("nothing to see here") is None
    assert read("") is None
    assert read(None) is None


def test_first_with_advance_button_selects_candidate() -> None:
    from unittest.mock import MagicMock

    pick = _assert_helpers()["first_with_advance_button"]

    def item(*labels):
        buttons = []
        for label in labels:
            button = MagicMock()
            button.inner_text.return_value = label
            buttons.append(button)
        candidate = MagicMock()
        candidate.get_by_role.return_value.all.return_value = buttons
        return candidate

    plain = item("Delete")
    advancable = item("Delete", "Advance to Ready")
    assert pick([plain, advancable]) is advancable
    assert pick([plain]) is None
    assert pick([]) is None
    assert pick(None) is None
    broken = MagicMock()
    broken.get_by_role.side_effect = Exception("stale")
    assert pick([broken, advancable]) is advancable
    flaky = MagicMock()
    flaky.inner_text.side_effect = Exception("detached")
    steady = MagicMock()
    steady.inner_text.return_value = "Mark Served"
    mixed = MagicMock()
    mixed.get_by_role.return_value.all.return_value = [flaky, steady]
    assert pick([mixed]) is mixed


_WALK_FIXTURE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Walker fixture</title></head>
<body><h1>Walker Diner</h1>
<section aria-label="Kitchen queue"><ul>
<li><p>WALK-1</p><p>Status: Received</p><button>Advance to Preparing</button></li>
</ul></section>
<section aria-label="Central order board"><ul>
<li><p>WALK-1</p><p>Status: Received</p><select><option>Received</option><option>Preparing</option><option>Ready</option></select></li>
<li><p>WALK-2</p><p>Status: Ready</p><p>No controls here</p></li>
</ul></section>
</body></html>"""


def test_walker_helpers_resolve_fixture_controls_in_real_browser() -> None:
    """Walker control resolution against real Chromium locators: the
    select rendering resolves via first_with_select, the button
    rendering via first_with_advance_button, and the control-less
    rendering resolves via neither (terminal shape). Skips cleanly
    when no browser is installed."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    helpers = _assert_helpers()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        try:
            page = browser.new_page()
            page.set_content(_WALK_FIXTURE_HTML)
            candidates = helpers["order_items"](page, "WALK-1").all()
            assert len(candidates) == 2
            selected = helpers["first_with_select"](candidates)
            assert selected is not None
            assert selected.locator("select").count() == 1
            assert selected.locator("button").count() == 0
            advanced = helpers["first_with_advance_button"](candidates)
            assert advanced is not None
            assert advanced.get_by_role("button").count() == 1
            assert advanced.locator("select").count() == 0
            sel = selected.locator("select").first
            opts = []
            for opt in sel.locator("option").all():
                text = (opt.inner_text() or "").strip()
                key = ((opt.get_attribute("value") or "") or text).strip()
                opts.append((key, bool(opt.is_disabled())))
            assert helpers["next_forward_option"](opts, sel.input_value()) == "Preparing"
            terminal = helpers["order_items"](page, "WALK-2").all()
            assert len(terminal) == 1
            assert helpers["first_with_select"](terminal) is None
            assert helpers["first_with_advance_button"](terminal) is None
        finally:
            browser.close()
