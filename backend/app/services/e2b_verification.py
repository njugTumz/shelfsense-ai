"""Deterministic E2B verification: build, serve, and functionally test a
React application inside an isolated sandbox.

Pattern follows the verified ``scripts/smoke/e2b_react.py`` approach
(node check, file upload, install, build, background preview, HTTP poll,
bundle evidence) with per-check records instead of a single PASS/FAIL.
The sandbox is always killed. No check reports PASS unless it executed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import httpx

from app.core.config import get_settings
from app.core.store import utc_now_iso
from app.demo.restaurant_app import (
    APP_ROOT,
    BUNDLE_EVIDENCE_STRINGS,
    ORDER_LOGIC_TEST_MJS,
)
from app.models.verification import (
    BrowserStepStatus,
    BrowserTestResult,
    CheckCategory,
    CheckStatus,
    E2BVerification,
    SandboxDiagnostics,
    VerificationCheck,
    has_no_browser_evidence,
    summarize_e2b,
)
from app.services.browser_workflow import (
    STEP_CHECK_NAMES,
    _is_transport_error,
    _with_transport_retry,
    browser_steps_to_checks,
    determine_required_steps,
    run_browser_tests,
    unavailable_browser_checks,
)

PORT = 5173
SANDBOX_TIMEOUT_S = 1200
COMMAND_TIMEOUT_S = 600
PREVIEW_POLL_S = 90
HTTP_TIMEOUT_S = 10.0
DETAIL_BUDGET = 3000
IN_SANDBOX_PREVIEW_URL = f"http://localhost:{PORT}"
#: One diagnostics snapshot: RAM + swap, top memory consumers, swap
#: devices, OOM-killer lines. Each section carries its own 2>&1 so a
#: missing tool (or a denied dmesg) degrades to a visible note
#: instead of blanking the snapshot.
DIAGNOSTICS_COMMAND = (
    "echo '=== free -m ==='; free -m 2>&1; "
    "echo '=== top mem (ps) ==='; ps aux --sort=-%mem 2>&1 | head -20; "
    "echo '=== swaps ==='; cat /proc/swaps 2>&1; "
    "echo '=== dmesg oom ==='; dmesg 2>&1 | grep -i -E 'oom|killed process|out of memory' | tail -20; "
    "echo '=== uptime ==='; uptime 2>&1"
)
DIAGNOSTICS_TIMEOUT_S = 60
DIAGNOSTICS_BUDGET = 4000


def _capture_sandbox_diagnostics(run, phase: str) -> str:
    """One diagnostics snapshot via an independent sandbox command.

    ``run`` is the (label, cmd, **kwargs) -> (result, err, seconds)
    helper. Never raises: every failure mode is recorded as text so a
    dead sandbox cannot break verification a second time.
    """
    try:
        result, err, dt = run(
            f"sandbox diagnostics ({phase})",
            DIAGNOSTICS_COMMAND,
            timeout=DIAGNOSTICS_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 - record, never raise
        return f"diagnostics capture failed: {type(exc).__name__}: {exc}"
    if err:
        return f"diagnostics command failed in {dt:.1f}s:\n{err}"[:DIAGNOSTICS_BUDGET]
    out = getattr(result, "stdout", "") or ""
    return f"captured in {dt:.1f}s:\n{out}"[:DIAGNOSTICS_BUDGET]


def _run_browser_phase(sbx, run, emit, required_steps):
    """Browser setup + script with diagnostics bracketing the script.

    Returns (browser_result, phase_checks, diagnostics). The after
    snapshot runs in a finally, so even a raised script call leaves a
    before/after pair (each independently best-effort).
    """
    emit("browser_tests", "Running real browser workflow tests…")
    setup_error = ""
    for label, cmd, cmd_timeout in BROWSER_SETUP_COMMANDS:
        _, err, _ = run(label, cmd, timeout=cmd_timeout)
        if err:
            setup_error = err
            break
    phase_checks: list[VerificationCheck] = []
    diagnostics = SandboxDiagnostics()
    if setup_error:
        browser_result = BrowserTestResult(
            available=False, error=f"Browser setup failed: {setup_error}"[:800])
        phase_checks.extend(
            unavailable_browser_checks(required_steps, browser_result.error))
        return browser_result, phase_checks, diagnostics
    diagnostics.before_browser = _capture_sandbox_diagnostics(run, "before")
    try:
        browser_result = run_browser_tests(sbx, IN_SANDBOX_PREVIEW_URL)
    finally:
        diagnostics.after_browser = _capture_sandbox_diagnostics(run, "after")
    if not browser_result.available:
        phase_checks.extend(
            unavailable_browser_checks(required_steps, browser_result.error))
    else:
        phase_checks.extend(
            browser_steps_to_checks(browser_result, required_steps))
    return browser_result, phase_checks, diagnostics

#: FIELD-fixed browser setup (the model supplies no shell commands).
BROWSER_SETUP_COMMANDS = (
    ("pip install playwright", "pip install -q playwright", 600),
    ("playwright install firefox", "python3 -m playwright install firefox", 900),
    ("playwright install-deps firefox", "python3 -m playwright install-deps firefox", 900),
)


def _check(
    name: str,
    category: CheckCategory,
    ok: bool,
    detail: str,
    latency_s: float,
    required: bool = True,
) -> VerificationCheck:
    return VerificationCheck(
        name=name,
        category=category,
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        required=required,
        detail=detail[:DETAIL_BUDGET],
        latency_s=round(latency_s, 1),
        completed_at=utc_now_iso(),
    )


def _skipped(name: str, category: CheckCategory, detail: str) -> VerificationCheck:
    return VerificationCheck(
        name=name,
        category=category,
        status=CheckStatus.SKIPPED,
        required=False,
        detail=detail[:DETAIL_BUDGET],
    )


def verify_in_e2b(
    files: dict[str, str],
    acceptance_criteria: list[str] | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> E2BVerification:
    """Build and verify ``files`` (absolute sandbox path -> content).

    Restaurant functional checks apply when the manifest carries the demo
    workflow files; otherwise they are honestly SKIPPED, never faked.
    Browser workflow steps are required only when the acceptance criteria
    demand them (see browser_workflow.determine_required_steps).
    ``progress`` receives (step_key, label) before each long stage so the
    UI can poll real progress; it is best-effort and never fails checks.
    """

    def emit(step: str, label: str) -> None:
        if progress is None:
            return
        try:
            progress(step, label)
        except Exception:  # noqa: BLE001 - progress must never break verification
            pass
    started = time.perf_counter()
    settings = get_settings()
    if not settings.e2b_configured:
        return E2BVerification(
            available=False,
            passed=False,
            error="E2B unavailable: E2B_API_KEY is not configured.",
            latency_s=round(time.perf_counter() - started, 1),
        )
    try:
        from e2b import Sandbox
    except ImportError:
        return E2BVerification(
            available=False,
            passed=False,
            error="E2B unavailable: the e2b package is not installed.",
            latency_s=round(time.perf_counter() - started, 1),
        )

    checks: list[VerificationCheck] = []
    sbx = None
    preview_url = ""
    transport_retries = 0
    try:
        emit("e2b_sandbox", "Preparing the E2B sandbox…")
        t0 = time.perf_counter()
        sbx, created_retries, create_error = _with_transport_retry(
            lambda: Sandbox.create(timeout=SANDBOX_TIMEOUT_S, api_key=settings.e2b_api_key)
        )
        transport_retries += created_retries
        if create_error is not None:
            raise create_error
        checks.append(
            _check(
                "sandbox_create",
                CheckCategory.BUILD,
                True,
                f"sandbox {sbx.sandbox_id} created (kill cap {SANDBOX_TIMEOUT_S}s)",
                time.perf_counter() - t0,
                required=False,
            )
        )

        def run(label: str, cmd: str, **kwargs):
            """commands.run raises (carrying stdout/stderr) on non-zero exit."""
            t0 = time.perf_counter()
            try:
                result = sbx.commands.run(cmd, **kwargs)
                return result, "", time.perf_counter() - t0
            except Exception as exc:  # noqa: BLE001 - SDK raises CommandExitException
                out = getattr(exc, "stdout", "") or ""
                err = getattr(exc, "stderr", "") or ""
                code = getattr(exc, "exit_code", "?")
                return (
                    None,
                    f"{label} exit={code}: {exc}\nstdout={out[-1500:]}\nstderr={err[-1500:]}",
                    time.perf_counter() - t0,
                )

        node, node_err, dt = run("node_runtime", "node --version && npm --version", timeout=60)
        checks.append(
            _check(
                "node_runtime",
                CheckCategory.BUILD,
                node is not None,
                (node.stdout.strip() if node else node_err),
                dt,
            )
        )
        if node is None:
            return _done(checks, preview_url, "", started, "No node runtime in sandbox.")

        t0 = time.perf_counter()
        for path, data in files.items():
            _, uploaded_retries, upload_error = _with_transport_retry(
                lambda p=path, d=data: sbx.files.write(p, d)
            )
            transport_retries += uploaded_retries
            if upload_error is not None:
                raise upload_error
        checks.append(
            _check(
                "files_uploaded",
                CheckCategory.BUILD,
                True,
                f"{len(files)} files uploaded under {APP_ROOT}",
                time.perf_counter() - t0,
                required=False,
            )
        )

        emit("e2b_install", "Installing dependencies in E2B…")
        install, install_err, dt = run(
            "npm_install", "npm install --no-audit --no-fund", cwd=APP_ROOT, timeout=COMMAND_TIMEOUT_S
        )
        checks.append(
            _check(
                "npm_install",
                CheckCategory.BUILD,
                install is not None,
                "dependencies installed" if install else install_err,
                dt,
            )
        )
        if install is None:
            return _done(checks, preview_url, sbx.sandbox_id, started, "npm install failed.")

        emit("e2b_build", "Building the React application…")
        build, build_err, dt = run("build", "npm run build", cwd=APP_ROOT, timeout=COMMAND_TIMEOUT_S)
        checks.append(
            _check(
                "production_build",
                CheckCategory.BUILD,
                build is not None,
                "production build succeeded" if build else build_err,
                dt,
            )
        )
        if build is None:
            return _done(checks, preview_url, sbx.sandbox_id, started, "Production build failed.")

        emit("e2b_preview", "Starting the preview server…")
        t0 = time.perf_counter()
        sbx.commands.run(
            "npm run preview -- --port 5173 --host 0.0.0.0",
            cwd=APP_ROOT,
            background=True,
            timeout=60,
        )
        preview_url = f"https://{sbx.get_host(PORT)}"

        body, status = "", 0
        deadline = time.time() + PREVIEW_POLL_S
        while time.time() < deadline:
            try:
                resp = httpx.get(preview_url, timeout=HTTP_TIMEOUT_S, follow_redirects=True)
                status, body = resp.status_code, resp.text
                if status == 200 and '<div id="root">' in body:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(3)
        serve_ok = status == 200 and '<div id="root">' in body
        checks.append(
            _check(
                "preview_serves",
                CheckCategory.RUNTIME,
                serve_ok,
                f"preview HTTP {status}, app root present" if serve_ok else f"preview HTTP {status}: {body[:500]}",
                time.perf_counter() - t0,
            )
        )
        if not serve_ok:
            return _done(checks, preview_url, sbx.sandbox_id, started, "Preview did not serve the app.")

        t0 = time.perf_counter()
        asset_paths = sorted(set(re.findall(r'(?:src|href)="(/assets/[^"]+)"', body)))
        missing = []
        for asset in asset_paths:
            try:
                asset_resp = httpx.get(f"{preview_url}{asset}", timeout=HTTP_TIMEOUT_S)
                if asset_resp.status_code != 200:
                    missing.append(f"{asset} -> HTTP {asset_resp.status_code}")
            except httpx.HTTPError as exc:
                missing.append(f"{asset} -> {type(exc).__name__}")
        checks.append(
            _check(
                "assets_resolve",
                CheckCategory.QUALITY,
                not missing,
                f"{len(asset_paths)} referenced assets all HTTP 200"
                if not missing
                else f"missing assets: {'; '.join(missing)}",
                time.perf_counter() - t0,
            )
        )

        bundle = ""
        for asset in asset_paths:
            if asset.endswith(".js"):
                try:
                    bundle += httpx.get(f"{preview_url}{asset}", timeout=HTTP_TIMEOUT_S).text
                except httpx.HTTPError:
                    pass
        found = [s for s in BUNDLE_EVIDENCE_STRINGS if s in bundle]
        missing_evidence = [s for s in BUNDLE_EVIDENCE_STRINGS if s not in bundle]
        demo_logic = f"{APP_ROOT}/src/lib/orderLogic.js" in files
        if demo_logic:
            checks.append(
                _check(
                    "order_workflow_present",
                    CheckCategory.FUNCTIONAL,
                    not missing_evidence,
                    f"static bundle evidence ({len(bundle)} bytes): found {found}"
                    + (f"; MISSING {missing_evidence}" if missing_evidence else ""),
                    0.0,
                )
            )
        else:
            checks.append(
                _skipped(
                    "order_workflow_present",
                    CheckCategory.FUNCTIONAL,
                    "Manifest has no demo workflow files; static evidence check skipped.",
                )
            )

        if demo_logic:
            t0 = time.perf_counter()
            sbx.files.write(f"{APP_ROOT}/orderLogic.check.mjs", ORDER_LOGIC_TEST_MJS)
            logic, logic_err, _ = run(
                "order_logic", "node orderLogic.check.mjs", cwd=APP_ROOT, timeout=120
            )
            logic_ok = logic is not None and "ORDER LOGIC OK" in (logic.stdout or "")
            checks.append(
                _check(
                    "status_transitions_execute",
                    CheckCategory.FUNCTIONAL,
                    logic_ok,
                    "node executed shipped orderLogic.js: forward steps pass, "
                    "skips/backwards/invalid rejected, counts correct"
                    if logic_ok
                    else (logic_err or f"unexpected output: {(logic.stdout if logic else '')[:500]}"),
                    time.perf_counter() - t0,
                )
            )
        else:
            checks.append(
                _skipped(
                    "status_transitions_execute",
                    CheckCategory.FUNCTIONAL,
                    "Manifest has no src/lib/orderLogic.js; transition test skipped.",
                )
            )

        required_steps = determine_required_steps(acceptance_criteria)
        browser_result, phase_checks, diagnostics = _run_browser_phase(
            sbx, run, emit, required_steps)
        checks.extend(phase_checks)

        browser_failed = bool(browser_result is not None and browser_result.transport_failed)
        browser_timed_out = bool(
            browser_result is not None and browser_result.script_timeout)
        transport_retries += (
            browser_result.transport_retries if browser_result is not None else 0
        )
        passed = summarize_e2b(checks)
        if browser_failed and browser_result is not None:
            error = (
                f"E2B transport failed after {browser_result.transport_retries + 1} "
                f"attempt(s): {browser_result.error}"
            )
        elif browser_timed_out and browser_result is not None:
            ran = sum(1 for s in browser_result.steps
                      if s.status != BrowserStepStatus.NOT_RUN)
            error = (
                f"Verification timed out: {ran} of {len(STEP_CHECK_NAMES)} "
                "browser steps ran; the rest were not attempted "
                "(re-run verification)."
            )
        elif has_no_browser_evidence(E2BVerification(
                available=True, passed=passed, checks=checks,
                browser=browser_result)):
            error = (
                "Browser phase produced no executed steps; the app built "
                "and the preview served (re-run verification)."
            )
        else:
            error = "" if passed else "One or more required checks failed."
        return _done(
            checks,
            preview_url,
            sbx.sandbox_id,
            started,
            error,
            browser=browser_result,
            transport_failed=browser_failed,
            transport_retries=transport_retries,
            script_timeout=browser_timed_out,
            diagnostics=diagnostics,
        )
    except Exception as exc:  # noqa: BLE001 - record the real failure, then kill
        if _is_transport_error(exc):
            checks.append(
                VerificationCheck(
                    name="sandbox_exception",
                    category=CheckCategory.RUNTIME,
                    status=CheckStatus.UNAVAILABLE,
                    required=True,
                    detail=f"E2B transport failed: {type(exc).__name__}: {exc}"[:800],
                )
            )
        else:
            checks.append(
                _check(
                    "sandbox_exception",
                    CheckCategory.RUNTIME,
                    False,
                    f"{type(exc).__name__}: {exc}",
                    0.0,
                )
            )
        sandbox_id = ""
        try:
            sandbox_id = sbx.sandbox_id if sbx is not None else ""
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            failure_diag = _capture_sandbox_diagnostics(run, "after-failure")
        except Exception:  # noqa: BLE001 - run unbound if create failed
            failure_diag = ""
        return _done(
            checks, preview_url, sandbox_id, started,
            f"E2B run failed: {type(exc).__name__}: {exc}",
            transport_failed=_is_transport_error(exc),
            transport_retries=transport_retries,
            diagnostics=SandboxDiagnostics(after_browser=failure_diag),
        )
    finally:
        if sbx is not None:
            try:
                sbx.kill()
            except Exception:  # noqa: BLE001, S110 - kill is best-effort; timeout caps cost
                pass


def _done(
    checks: list[VerificationCheck],
    preview_url: str,
    sandbox_id: str,
    started: float,
    error: str,
    browser: BrowserTestResult | None = None,
    transport_failed: bool = False,
    transport_retries: int = 0,
    script_timeout: bool = False,
    diagnostics: SandboxDiagnostics | None = None,
) -> E2BVerification:
    passed = summarize_e2b(checks)
    if transport_failed or script_timeout:
        passed = False
    return E2BVerification(
        available=True,
        passed=passed,
        checks=checks,
        preview_url=preview_url,
        sandbox_id=sandbox_id,
        error="" if passed else error,
        latency_s=round(time.perf_counter() - started, 1),
        browser=browser,
        transport_failed=transport_failed,
        transport_retries=transport_retries,
        script_timeout=script_timeout,
        diagnostics=diagnostics or SandboxDiagnostics(),
    )
