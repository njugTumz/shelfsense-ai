"""Keyless tests: INCONCLUSIVE verdict for sandbox-transport failures.

Transport errors (ConnectError, stream errors, transient E2B SDK
failures) retry once, then yield INCONCLUSIVE -- never FAILED.
Executed assertion failures keep their current behaviour.
"""

import pytest

from app.core.config import REPO_ROOT
from app.models.verification import (
    AuditFinding,
    AuditResult,
    AuditVerdict,
    BROWSER_CHECK_NAMES,
    BrowserStep,
    BrowserStepStatus,
    BrowserTestResult,
    CheckCategory,
    CheckStatus,
    E2BVerification,
    VerificationCheck,
    VerificationStatus,
    compute_verdict,
    has_no_browser_evidence,
)
from app.services.browser_workflow import (
    BROWSER_SCRIPT_TIMEOUT_S,
    STEP_CHECK_NAMES,
    _is_polling_timeout,
    _is_script_budget_timeout,
    _is_transport_error,
    _with_transport_retry,
    run_browser_tests,
)


def _check(name, status, required=True):
    return VerificationCheck(
        name=name,
        category=CheckCategory.BUILD,
        status=status,
        required=required,
    )


def _audit(verdict=None, findings=None):
    return AuditResult(
        provider="spark_adversarial",
        available=True,
        completed=True,
        verdict=verdict,
        findings=findings or [],
    )


def _finding(severity):
    return AuditFinding(
        severity=severity,
        category="requirements",
        finding="f",
        evidence="e",
        recommendation="r",
    )


def test_transport_taxonomy_httpx() -> None:
    import httpx

    for cls in (
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.PoolTimeout,
    ):
        assert _is_transport_error(cls("boom")) is True
    assert _is_transport_error(ValueError("boom")) is False
    assert _is_transport_error(AssertionError("boom")) is False


def test_transport_taxonomy_e2b() -> None:
    e2b = pytest.importorskip("e2b")

    for name in (
        "SandboxUnreachableException",
        "SandboxNotRunningException",
        "SandboxNotFoundException",
        "TimeoutException",
        "RateLimitException",
        "ServiceBusyException",
        "FileUploadException",
    ):
        assert _is_transport_error(getattr(e2b, name)("boom")) is True
    # A command that ran and exited non-zero is measured evidence.
    assert _is_transport_error(ValueError("exit=1")) is False
    assert _is_transport_error(e2b.AuthenticationException("boom")) is False


def test_retry_helper_retries_transport_once() -> None:
    import httpx

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return "ok"

    slept = []
    value, retries, error = _with_transport_retry(
        flaky, delay_s=7, sleep=slept.append)
    assert (value, retries, error) == ("ok", 1, None)
    assert slept == [7]
    assert len(calls) == 2


def test_retry_helper_counts_terminal_failures() -> None:
    import httpx

    def always():
        raise httpx.ConnectError("down")

    value, retries, error = _with_transport_retry(always, delay_s=0, sleep=lambda s: None)
    assert value is None
    assert retries == 1
    assert isinstance(error, httpx.ConnectError)

    def app_error():
        raise ValueError("bad")

    value, retries, error = _with_transport_retry(app_error, delay_s=0, sleep=lambda s: None)
    assert value is None
    assert retries == 0
    assert isinstance(error, ValueError)


def _stub_commands(script):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    sbx = MagicMock()
    sbx.commands.run.side_effect = script
    sbx.files.read.side_effect = FileNotFoundError("no screenshots on stub")
    return sbx


def _report_stdout():
    import json

    return "BROWSER_JSON:" + json.dumps({
        "browser": "stub",
        "steps": [{"name": "load", "status": "pass", "detail": "", "duration_ms": 1}],
        "screenshots": [],
    })


def test_browser_run_does_not_retry_transport() -> None:
    """A mid-stream transport error can leave the first run alive, so a
    retry could overlap it: transport goes straight to INCONCLUSIVE."""
    import httpx

    sbx = _stub_commands([httpx.ConnectError("stream error received")])
    result = run_browser_tests(sbx, "http://stub/")
    assert result.available is False
    assert result.transport_failed is True
    assert result.transport_retries == 0
    assert "ConnectError" in result.error
    assert "not retried; re-run verification" in result.error
    assert sbx.commands.run.call_count == 1


def test_browser_run_does_not_retry_app_failures() -> None:
    sbx = _stub_commands([ValueError("bad invocation")])
    result = run_browser_tests(sbx, "http://stub/")
    assert result.available is False
    assert result.transport_failed is False
    assert result.transport_retries == 0
    assert sbx.commands.run.call_count == 1


def test_browser_run_without_report_is_measured_not_transport() -> None:
    from types import SimpleNamespace

    sbx = _stub_commands([SimpleNamespace(stdout="traceback, no json line")])
    result = run_browser_tests(sbx, "http://stub/")
    assert result.available is False
    assert result.transport_failed is False
    assert result.transport_retries == 0


def test_verdict_inconclusive_on_transport() -> None:
    clean = _audit(verdict=AuditVerdict.PASS)
    e2b = E2BVerification(
        available=True, passed=False,
        checks=[_check("browser_loads_app", CheckStatus.UNAVAILABLE)],
        transport_failed=True, transport_retries=1,
    )
    assert compute_verdict(e2b, clean) == (
        VerificationStatus.INCONCLUSIVE, "transport")


def test_verdict_required_failure_beats_transport() -> None:
    clean = _audit(verdict=AuditVerdict.PASS)
    e2b = E2BVerification(
        available=True, passed=False,
        checks=[
            _check("browser_creates_order", CheckStatus.FAIL),
            _check("browser_loads_app", CheckStatus.UNAVAILABLE),
        ],
        transport_failed=True, transport_retries=1,
    )
    assert compute_verdict(e2b, clean) == (VerificationStatus.FAILED, "runtime")


def test_verdict_inconclusive_beats_audit() -> None:
    blocking = _audit(
        verdict=AuditVerdict.FAIL, findings=[_finding("critical")])
    e2b = E2BVerification(
        available=True, passed=False,
        checks=[_check("browser_loads_app", CheckStatus.UNAVAILABLE)],
        transport_failed=True, transport_retries=1,
    )
    assert compute_verdict(e2b, blocking) == (
        VerificationStatus.INCONCLUSIVE, "transport")


def test_script_budget_is_900s_and_sandbox_cap_unchanged() -> None:
    from app.services.e2b_verification import SANDBOX_TIMEOUT_S

    assert BROWSER_SCRIPT_TIMEOUT_S == 900
    assert SANDBOX_TIMEOUT_S == 1200


def test_script_budget_timeout_exact_type_only() -> None:
    e2b = pytest.importorskip("e2b")

    assert _is_script_budget_timeout(e2b.TimeoutException("deadline")) is True
    assert _is_script_budget_timeout(
        e2b.SandboxUnreachableException("gone")) is False
    assert _is_script_budget_timeout(
        e2b.SandboxNotRunningException("killed")) is False
    assert _is_script_budget_timeout(ValueError("x")) is False


def test_script_budget_timeout_never_retries() -> None:
    e2b = pytest.importorskip("e2b")

    sbx = _stub_commands([e2b.TimeoutException("deadline_exceeded")])
    result = run_browser_tests(sbx, "http://stub/")
    assert result.available is False
    assert result.transport_failed is False
    assert result.script_timeout is True
    assert result.transport_retries == 0
    assert sbx.commands.run.call_count == 1
    assert "timed out after 900s" in result.error
    assert "not retried" in result.error


def test_sandbox_state_subclass_goes_straight_to_inconclusive() -> None:
    e2b = pytest.importorskip("e2b")

    sbx = _stub_commands([e2b.SandboxUnreachableException("proxy 502")])
    result = run_browser_tests(sbx, "http://stub/")
    assert result.available is False
    assert result.transport_failed is True
    assert result.transport_retries == 0
    assert "not retried; re-run verification" in result.error
    assert sbx.commands.run.call_count == 1


def test_httpx_timeouts_are_transport_and_polling() -> None:
    """Real-class hierarchy lock (httpx 0.28): timeouts subclass BOTH
    TransportError and TimeoutException, so the invoke site must check
    polling-timeout BEFORE the transport taxonomy to withhold retry."""
    import httpx

    for cls in (
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
    ):
        assert issubclass(cls, httpx.TransportError)
        assert issubclass(cls, httpx.TimeoutException)
        assert _is_transport_error(cls("x")) is True
        assert _is_polling_timeout(cls("x")) is True
    assert _is_polling_timeout(httpx.ConnectError("x")) is False
    assert _is_polling_timeout(httpx.ReadError("x")) is False
    assert _is_polling_timeout(ValueError("x")) is False


def test_polling_timeout_never_retries() -> None:
    import httpx

    for cls in (
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
    ):
        sbx = _stub_commands([cls("poll timed out")])
        result = run_browser_tests(sbx, "http://stub/")
        assert result.available is False
        assert result.transport_failed is True
        assert result.transport_retries == 0
        assert sbx.commands.run.call_count == 1
        assert "polling the sandbox timed out" in result.error
        assert "not retried" in result.error


def test_verdict_timeout_surfaces_inconclusive() -> None:
    clean = _audit(verdict=AuditVerdict.PASS)
    e2b = E2BVerification(
        available=True, passed=False,
        checks=[_check("browser_loads_app", CheckStatus.UNAVAILABLE)],
        error="Browser script timed out: verification timed out after 900s",
        transport_failed=True, transport_retries=0,
    )
    assert compute_verdict(e2b, clean) == (
        VerificationStatus.INCONCLUSIVE, "transport")


def test_verdict_unavailable_without_transport_stays_failed() -> None:
    clean = _audit(verdict=AuditVerdict.PASS)
    e2b = E2BVerification(
        available=True, passed=False,
        checks=[_check("browser_loads_app", CheckStatus.UNAVAILABLE)],
    )
    assert compute_verdict(e2b, clean) == (VerificationStatus.FAILED, "runtime")


def _frontend(path: str) -> str:
    return (REPO_ROOT / "frontend" / "src" / path).read_text()


def test_inconclusive_ui_contract() -> None:
    """Static contract: the INCONCLUSIVE verdict renders neutrally with a
    re-run pointer, and unmeasured checks never render red. (The
    frontend has no test runner; this locks the copy and tones.)"""
    results = _frontend("components/VerificationResults.tsx")
    assert 'INCONCLUSIVE: { title: "Inconclusive", tone: "text-stone-600" }' in results
    assert 'result.status === "INCONCLUSIVE"' in results
    assert "re-run verification" in results
    assert "not proven broken" in results
    # Budget exhaustion is its own copy, never "infrastructure failed".
    assert "result.e2b.script_timeout" in results
    assert "Verification timed out before all browser steps ran" in results
    assert "infrastructure failed" in results
    # Zero-evidence runs get their own copy keyed off the reason.
    assert 'result.reason === "no_browser_evidence"' in results
    assert "Verification ended without browser evidence" in results
    # Unmeasured checks fall through to the neutral glyph/tone: no
    # special-case may paint them red.
    assert 'status === "unavailable"' not in results
    assert 'return "○";' in results
    assert 'return "text-stone-500";' in results

    api = _frontend("services/api.ts")
    assert '"INCONCLUSIVE"' in api
    assert "transport_failed: boolean" in api
    assert "transport_retries: number" in api
    assert "script_timeout: boolean" in api
    assert '"not_run"' in api

    source = _frontend("components/WorkflowProgress.tsx")
    line = next(
        line for line in source.splitlines() if "const TERMINAL = [" in line)
    assert '"INCONCLUSIVE"' in line, "WorkflowProgress terminal list"
    # InterviewFlow aliases the shared list (single source of truth);
    # the shared list itself must carry INCONCLUSIVE.
    flow = _frontend("components/InterviewFlow.tsx")
    assert "const TERMINAL_BUILD_STATES = TERMINAL_STATUSES;" in flow
    shared = _frontend("services/api.ts")
    start = shared.index("export const TERMINAL_STATUSES = [")
    end = shared.index("];", start)
    assert '"INCONCLUSIVE"' in shared[start:end]


def test_verdict_script_timeout_surfaces_inconclusive() -> None:
    clean = _audit(verdict=AuditVerdict.PASS)
    e2b = E2BVerification(
        available=True, passed=False,
        checks=[_check("browser_loads_app", CheckStatus.PASS),
                _check("browser_creates_order", CheckStatus.UNAVAILABLE)],
        script_timeout=True,
    )
    assert compute_verdict(e2b, clean) == (
        VerificationStatus.INCONCLUSIVE, "script_timeout")


def test_verdict_required_fail_wins_over_script_timeout() -> None:
    """An executed required failure stays FAILED even when the deadline
    also fired: observed breakage outranks unmeasured remainder."""
    clean = _audit(verdict=AuditVerdict.PASS)
    e2b = E2BVerification(
        available=True, passed=False,
        checks=[_check("browser_loads_app", CheckStatus.FAIL)],
        script_timeout=True,
    )
    assert compute_verdict(e2b, clean) == (VerificationStatus.FAILED, "runtime")


def test_verdict_transport_wins_over_script_timeout() -> None:
    """Both flags cannot co-occur (report vs exception paths), but the
    order is pinned: transport first."""
    clean = _audit(verdict=AuditVerdict.PASS)
    e2b = E2BVerification(
        available=True, passed=False, checks=[],
        transport_failed=True, script_timeout=True,
    )
    assert compute_verdict(e2b, clean) == (
        VerificationStatus.INCONCLUSIVE, "transport")


def test_transport_taxonomy_httpcore() -> None:
    """Raw httpcore leaks (the E2B SDK guards httpx types only) are
    transport, including timeouts. Non-transport stays out."""
    import httpcore

    for cls in (
        httpcore.ConnectError,
        httpcore.ReadError,
        httpcore.WriteError,
        httpcore.TimeoutException,
        httpcore.ConnectTimeout,
        httpcore.ReadTimeout,
        httpcore.WriteTimeout,
        httpcore.PoolTimeout,
    ):
        assert _is_transport_error(cls("boom")) is True, cls
    assert _is_transport_error(ValueError("boom")) is False
    assert _is_transport_error(httpcore.ProtocolError("boom")) is False


def test_browser_run_httpcore_connect_error_is_transport() -> None:
    """Replay of the 3306-rerun stream break at the unit level: a raw
    httpcore.ConnectError goes straight to INCONCLUSIVE-transport."""
    import httpcore

    sbx = _stub_commands([httpcore.ConnectError("stream error received")])
    result = run_browser_tests(sbx, "http://stub/")
    assert result.available is False
    assert result.transport_failed is True
    assert result.script_timeout is False
    assert result.transport_retries == 0
    assert "not retried; re-run verification" in result.error
    assert sbx.commands.run.call_count == 1


def test_browser_check_names_match_step_map() -> None:
    """Contract: the verdict's browser-check set equals the driver's
    step->check map values, so the structural rule cannot drift."""
    assert set(STEP_CHECK_NAMES.values()) == set(BROWSER_CHECK_NAMES)
    assert len(BROWSER_CHECK_NAMES) == 9


def _no_evidence_e2b(**overrides):
    """Rerun-shaped E2B result: build green, browser checks unmeasured."""
    checks = [
        _check("sandbox_create", CheckStatus.PASS, required=False),
        _check("node_runtime", CheckStatus.PASS),
        _check("production_build", CheckStatus.PASS),
        _check("preview_serves", CheckStatus.PASS),
        _check("browser_loads_app", CheckStatus.UNAVAILABLE),
        _check("browser_creates_order", CheckStatus.UNAVAILABLE),
        _check("browser_advances_status", CheckStatus.UNAVAILABLE),
    ]
    browser = BrowserTestResult(available=False, steps=[])
    values = dict(available=True, passed=False, checks=checks, browser=browser)
    values.update(overrides)
    return E2BVerification(**values)


def test_no_browser_evidence_predicate() -> None:
    assert has_no_browser_evidence(_no_evidence_e2b()) is True
    # An executed failure is evidence: the rule stays out.
    failed_step = _no_evidence_e2b(browser=BrowserTestResult(
        available=False, steps=[BrowserStep(
            name="load", status=BrowserStepStatus.FAIL, detail="boom")]))
    assert has_no_browser_evidence(failed_step) is False
    # No browser result at all: cannot confirm, stays out.
    assert has_no_browser_evidence(_no_evidence_e2b(browser=None)) is False
    # A required non-browser check unmeasured: not a browser-only gap.
    missing_build = _no_evidence_e2b(checks=[
        _check("production_build", CheckStatus.UNAVAILABLE),
        _check("preview_serves", CheckStatus.PASS),
        _check("browser_loads_app", CheckStatus.UNAVAILABLE),
    ])
    assert has_no_browser_evidence(missing_build) is False


def test_verdict_no_browser_evidence_matrix() -> None:
    clean = _audit(verdict=AuditVerdict.PASS)
    assert compute_verdict(_no_evidence_e2b(), clean) == (
        VerificationStatus.INCONCLUSIVE, "no_browser_evidence")
    # Executed required failures still win.
    failed = _no_evidence_e2b(checks=[
        _check("production_build", CheckStatus.PASS),
        _check("preview_serves", CheckStatus.PASS),
        _check("browser_loads_app", CheckStatus.FAIL),
    ])
    assert compute_verdict(failed, clean) == (VerificationStatus.FAILED, "runtime")
    # Specific flags keep precedence over the structural rule.
    assert compute_verdict(
        _no_evidence_e2b(transport_failed=True), clean) == (
        VerificationStatus.INCONCLUSIVE, "transport")
    assert compute_verdict(
        _no_evidence_e2b(script_timeout=True), clean) == (
        VerificationStatus.INCONCLUSIVE, "script_timeout")


def test_replay_3306_rerun_is_inconclusive() -> None:
    """The stored 3306-rerun result replays to INCONCLUSIVE through the
    structural rule (it verdict FAILED live only via the taxonomy gap)."""
    import json
    from pathlib import Path

    stored = Path("/home/izaktumz/field_evidence/3306-live-20261106-rerun/checks.json")
    if not stored.exists():
        pytest.skip("rerun evidence not present in this environment")
    payload = json.loads(stored.read_text())["verification"]
    e2b = E2BVerification(**payload["e2b"])
    audit = AuditResult(**payload["audit"])
    assert e2b.transport_failed is False
    assert e2b.script_timeout is False
    assert has_no_browser_evidence(e2b) is True
    assert compute_verdict(e2b, audit) == (
        VerificationStatus.INCONCLUSIVE, "no_browser_evidence")
