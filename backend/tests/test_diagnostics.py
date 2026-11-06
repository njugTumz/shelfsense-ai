"""Keyless tests: sandbox diagnostics bracketing the browser phase."""

from types import SimpleNamespace

import pytest

from app.models.verification import (
    BrowserStep,
    BrowserStepStatus,
    BrowserTestResult,
    CheckStatus,
)
from app.services import e2b_verification as e2b_module
from app.services.e2b_verification import (
    DIAGNOSTICS_BUDGET,
    DIAGNOSTICS_COMMAND,
    _capture_sandbox_diagnostics,
    _done,
    _run_browser_phase,
)


def _ok_run(calls, stdout="Mem: 473"):
    def run(label, cmd, **kwargs):
        calls.append((label, cmd, kwargs.get("timeout")))
        return SimpleNamespace(stdout=stdout), "", 1.2

    return run


def test_diagnostics_command_covers_required_sections() -> None:
    for section in ("free -m", "ps aux", "/proc/swaps", "dmesg", "oom"):
        assert section in DIAGNOSTICS_COMMAND


def test_capture_success_records_output_and_timing() -> None:
    text = _capture_sandbox_diagnostics(_ok_run([]), "before")
    assert "captured in 1.2s" in text
    assert "Mem: 473" in text


def test_capture_command_error_is_recorded_not_raised() -> None:
    def run(label, cmd, **kwargs):
        return None, "exit=1: boom", 0.3

    text = _capture_sandbox_diagnostics(run, "after")
    assert "diagnostics command failed in 0.3s" in text
    assert "boom" in text


def test_capture_raising_run_is_recorded_not_raised() -> None:
    def run(label, cmd, **kwargs):
        raise ConnectionError("sandbox gone")

    text = _capture_sandbox_diagnostics(run, "before")
    assert "diagnostics capture failed: ConnectionError: sandbox gone" in text


def test_capture_output_is_budgeted() -> None:
    text = _capture_sandbox_diagnostics(_ok_run([], "x" * 9000), "before")
    assert len(text) <= DIAGNOSTICS_BUDGET


def test_browser_phase_setup_failure_skips_diagnostics_and_script(monkeypatch) -> None:
    calls = []

    def run(label, cmd, **kwargs):
        calls.append((label, cmd))
        return None, "exit=1: pip dead", 0.1

    sentinel = ModuleNotFoundError("must not run the script")
    monkeypatch.setattr(e2b_module, "run_browser_tests", _raising(sentinel))
    result, checks, diagnostics = _run_browser_phase(
        object(), run, lambda *a: None, {"load": True})
    assert result.available is False
    assert "Browser setup failed" in result.error
    assert all(c.status == CheckStatus.UNAVAILABLE for c in checks)
    assert diagnostics.before_browser == ""
    assert diagnostics.after_browser == ""
    assert all("free -m" not in cmd for _, cmd in calls)


def _raising(exc):
    def fake(*args, **kwargs):
        raise exc

    return fake


def test_browser_phase_captures_before_and_after(monkeypatch) -> None:
    calls = []
    steps = [BrowserStep(name="load", status=BrowserStepStatus.PASS,
                         detail="ok", duration_ms=1000)]
    monkeypatch.setattr(
        e2b_module, "run_browser_tests",
        lambda sbx, url: BrowserTestResult(available=True, steps=steps))
    result, checks, diagnostics = _run_browser_phase(
        object(), _ok_run(calls), lambda *a: None, {"load": True})
    assert result.available is True
    assert any(c.name == "browser_loads_app" and c.status == CheckStatus.PASS
               for c in checks)
    diag_calls = [label for label, cmd, _ in calls if cmd == DIAGNOSTICS_COMMAND]
    assert diag_calls == ["sandbox diagnostics (before)", "sandbox diagnostics (after)"]
    assert "Mem: 473" in diagnostics.before_browser
    assert "Mem: 473" in diagnostics.after_browser


def test_browser_phase_after_capture_runs_on_script_raise(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        e2b_module, "run_browser_tests",
        _raising(RuntimeError("script upload blew up")))
    with pytest.raises(RuntimeError, match="script upload blew up"):
        _run_browser_phase(object(), _ok_run(calls), lambda *a: None, {"load": True})
    diag_calls = [label for label, cmd, _ in calls if cmd == DIAGNOSTICS_COMMAND]
    assert diag_calls == ["sandbox diagnostics (before)", "sandbox diagnostics (after)"]


def test_done_threads_diagnostics() -> None:
    from app.models.verification import SandboxDiagnostics

    diag = SandboxDiagnostics(before_browser="b", after_browser="a")
    assert _done([], "", "", 0.0, "", diagnostics=diag).diagnostics is diag
    assert _done([], "", "", 0.0, "").diagnostics.before_browser == ""
