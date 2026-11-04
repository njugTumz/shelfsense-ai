"""Real browser interaction testing inside the E2B sandbox.

FIELD owns the test runner (Muse Spark never generates it). The script
below drives headless Firefox against the in-sandbox preview over
localhost, discovers controls by accessible roles with fallbacks, and
reports per-step JSON. The backend maps steps to verification checks:
required-by-spec workflows that are missing or broken FAIL; workflows
the spec never asks for SKIP (never pass).

Workflow ``restaurant``: load -> create -> advance x2 -> invalid ->
reload/persist -> responsive -> console. The console and responsive steps
are quality signals: they report honestly but never gate the verdict, so
older applications keep their behavior on re-verification. Other
workflows can be added as new scripts; the report/check mapping stays
shared.
"""

from __future__ import annotations

import base64
import json
import time

from app.models.verification import (
    BrowserScreenshot,
    BrowserStep,
    BrowserStepStatus,
    BrowserTestResult,
    CheckCategory,
    CheckStatus,
    VerificationCheck,
)

BROWSER_SCRIPT_PATH = "/tmp/browser_workflow.check.py"
BROWSER_ASSERT_PATH = "/tmp/browser_assert.py"
SCREENSHOT_JPG_QUALITY = 60

CREATE_KEYWORDS = ("create", "add", "new order", "entry", "submit", "form")
ADVANCE_KEYWORDS = ("status", "transition", "preparing", "ready", "move", "advance", "update")
INVALID_KEYWORDS = ("invalid", "reject")
PERSIST_KEYWORDS = ("persist", "reload", "refresh", "storage", "survive")

STEP_CHECK_NAMES = {
    "load": "browser_loads_app",
    "create": "browser_creates_order",
    "advance": "browser_advances_status",
    "invalid": "browser_rejects_invalid",
    "persist": "browser_persists_after_reload",
    "console": "browser_console_clean",
    "responsive": "browser_responsive_layout",
}

BROWSER_ASSERT_SOURCE = r'''
"""Status-display assertions for the E2B browser script (single source of truth).

The button-branch checks must accept any contract-compliant status rendering
(a bare badge OR prefixed text such as "Status: Preparing") while never
letting control wording fake status evidence: a placebo "Advance to X"
button that changes nothing must still fail.
"""
import re


def parse_button_target(button_name):
    """Target status stated by an advance control ('Advance to Preparing' -> 'Preparing')."""
    match = re.search(r"(?:to|mark)\s+(.+)$", button_name or "", re.I)
    return match.group(1).strip() if match else None


def display_text_without_buttons(item_text, button_texts):
    """Item text with control labels removed so buttons cannot fake status evidence."""
    display = item_text or ""
    for text in button_texts or []:
        if text:
            display = display.replace(text, " ")
    return display


def status_shown_in_display(display_text, expected):
    """Expected status appears as a whole word in non-control display text."""
    if not expected:
        return False
    return re.search(r"\b" + re.escape(expected) + r"\b", display_text or "") is not None


def button_moved_past(button_names_after, expected):
    """No remaining advance control still targets the reached status.

    Terminal states (no advance control left) count as moved past. This is
    only ever a secondary signal; the display check above is the proof.
    """
    for name in button_names_after or []:
        if parse_button_target(name) == expected:
            return False
    return True
'''

BROWSER_SCRIPT = r'''
"""FIELD-owned restaurant workflow test. argv: url marker. Prints BROWSER_JSON line."""
import json
import re
import sys
import time

from browser_assert import (
    button_moved_past,
    display_text_without_buttons,
    parse_button_target,
    status_shown_in_display,
)

URL, MARKER = sys.argv[1], sys.argv[2]
ADVANCE_BUTTON = re.compile("advance|next|move|mark|progress|forward", re.I)
SUBMIT_BUTTON = re.compile("add|create|submit|save|new order", re.I)


def main():
    from playwright.sync_api import sync_playwright

    steps = []
    shots = []

    def rec(name, status, detail="", ms=0):
        steps.append({"name": name, "status": status, "detail": detail[:800],
                      "duration_ms": int(ms)})

    def shot(page, name):
        try:
            path = f"/tmp/browser_{name}.jpg"
            page.screenshot(path=path, type="jpeg", quality=60)
            shots.append({"name": name, "path": path})
        except Exception:
            pass

    def timed():
        return time.time()

    def fresh_load():
        """Reload the page, falling back to a fresh navigation of the same
        URL when the reload itself wedges (observed as 60s stalls or
        NS_BINDING_ABORTED after an earlier aborted navigation). Both paths
        execute a full page load against the same origin store, so
        persistence assertions keep their meaning; the detail records which
        path ran. Returns "reload" or "navigated"; raises on total failure.
        """
        try:
            page.reload(wait_until="load")
            return "reload"
        except Exception:
            try:
                page.evaluate("() => window.stop()")
            except Exception:
                pass
            page.wait_for_timeout(1000)
            page.goto(URL, wait_until="load")
            return "navigated"

    def fill_create_form(scope, marker):
        """Fill every fillable control so realistic multi-field forms submit.

        Generated apps may ask for label + details + type; filling only the
        first box leaves required fields empty and the submit silently does
        nothing. Returns counts for diagnostics.
        """
        counts = {"text": 0, "number": 0, "check": 0}
        try:
            boxes = scope.get_by_role("textbox").all()
        except Exception:
            boxes = []
        if not boxes:
            try:
                boxes = scope.locator("input[type='text'], input:not([type]), textarea").all()
            except Exception:
                boxes = []
        for box in boxes:
            try:
                if box.is_visible() and not box.is_disabled():
                    box.fill(marker)
                    counts["text"] += 1
            except Exception:
                pass
        try:
            nums = scope.get_by_role("spinbutton").all()
        except Exception:
            nums = []
        for num in nums:
            try:
                if num.is_visible() and not num.is_disabled() and not num.input_value():
                    num.fill("1")
                    counts["number"] += 1
            except Exception:
                pass
        try:
            checks = scope.get_by_role("checkbox").all()
        except Exception:
            checks = []
        for check in checks:
            try:
                if check.is_visible() and not check.is_disabled() and not check.is_checked():
                    check.check()
                    counts["check"] += 1
            except Exception:
                pass
        return counts

    with sync_playwright() as pw:
        # E2B sandboxes have ~473MB RAM with vite + driver resident: keep
        # Firefox lean (single content process, no memory cache) so the
        # browser survives a full workflow run on realistic pages.
        browser = pw.firefox.launch(firefox_user_prefs={
            "dom.ipc.processCount": 1,
            "browser.cache.memory.enable": False,
        })
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        # Firefox in E2B loads vite apps slowly (healthy loads observed at
        # 13s+); 15s wrongly failed healthy apps, so allow a full minute.
        page.set_default_timeout(60000)
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text[:300])
                if msg.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(str(exc)[:300]))
        version = browser.version
        try:
            t0 = timed()
            page.goto(URL, wait_until="load")
            page.locator("h1").first.wait_for(state="visible")
            heading = page.locator("h1").first.inner_text()[:100]
            rec("load", "pass", f"loaded; heading: {heading}", (timed() - t0) * 1000)
        except Exception as exc:
            rec("load", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)
            for name in ("create", "advance", "invalid", "persist", "console", "responsive"):
                rec(name, "blocked", "page did not load")
            return {"browser": f"firefox {version}", "steps": steps, "screenshots": shots}

        identifier = None
        last_status = "Received"

        # --- create ---
        t0 = timed()
        try:
            form = page.locator("form").first
            scope = form if form.count() else page
            counts = fill_create_form(scope, MARKER)
            if counts["text"] == 0:
                rec("create", "not_applicable", "no text input found", (timed() - t0) * 1000)
            else:
                submit = scope.locator("button[type='submit']").first
                if submit.count() == 0:
                    submit = scope.get_by_role("button", name=SUBMIT_BUTTON).first
                if submit.count() == 0:
                    submit = scope.get_by_role("button").first
                if submit.count() == 0:
                    rec("create", "fail", "text input found but no submit button", (timed() - t0) * 1000)
                else:
                    submit.click()
                    page.get_by_text(MARKER).first.wait_for(state="visible")
                    identifier = MARKER
                    rec("create", "pass",
                        f"order created; marker visible in list (filled {counts['text']} text, "
                        f"{counts['number']} number, {counts['check']} check)",
                        (timed() - t0) * 1000)
                    shot(page, "created")
        except Exception as exc:
            shot(page, "create_failed")
            rec("create", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- advance Received -> Preparing -> Ready ---
        t0 = timed()
        try:
            if identifier is None:
                first_li = page.locator("li").first
                if first_li.count() == 0:
                    rec("advance", "not_applicable", "no order list items found", (timed() - t0) * 1000)
                else:
                    strong = first_li.locator("strong").first
                    identifier = (strong.inner_text() if strong.count() else first_li.inner_text()[:24]).strip()
                    if not identifier:
                        rec("advance", "fail", "first list item has no readable identifier", (timed() - t0) * 1000)
                    identifier = identifier or None
            if identifier is not None and not any(s["name"] == "advance" for s in steps):
                item = page.locator("li", has_text=identifier).first
                if item.count() == 0:
                    rec("advance", "fail", f"order item {identifier!r} not found", (timed() - t0) * 1000)
                else:
                    sel = item.locator("select").first
                    if sel.count() > 0:
                        chain = [sel.input_value()]
                        for _ in (1, 2):
                            # Options may carry machine values with human labels
                            # (<option value="active">Active</option>): advance by
                            # value, falling back to visible text when an option
                            # has no value attribute (value then equals text).
                            vals = []
                            for opt in sel.locator("option").all():
                                text = opt.inner_text()
                                vals.append(((opt.get_attribute("value") or "") or text).strip())
                            try:
                                nxt = vals[vals.index(chain[-1]) + 1]
                            except (ValueError, IndexError):
                                raise AssertionError(f"no further status option after {chain[-1]!r}")
                            sel.select_option(value=nxt)
                            page.wait_for_timeout(400)
                            if sel.input_value() != nxt:
                                raise AssertionError(f"control shows {sel.input_value()!r}, expected {nxt!r}")
                            chain.append(nxt)
                        last_status = chain[-1]
                        rec("advance", "pass", f"select: status advanced twice ({' -> '.join(chain)})",
                            (timed() - t0) * 1000)
                    else:
                        btn = item.get_by_role("button", name=ADVANCE_BUTTON).first
                        if btn.count() == 0:
                            rec("advance", "not_applicable", "order item has no status control (select or button)",
                                (timed() - t0) * 1000)
                        else:
                            chain = []
                            for _ in (1, 2):
                                item = page.locator("li", has_text=identifier).first
                                btn = item.get_by_role("button", name=ADVANCE_BUTTON).first
                                if btn.count() == 0:
                                    raise AssertionError("no advance button for next transition")
                                name = (btn.get_attribute("aria-label") or btn.inner_text() or "").strip()
                                expected = parse_button_target(name)
                                if not expected:
                                    raise AssertionError(f"advance button does not state next status: {name!r}")
                                btn.click()
                                page.wait_for_timeout(400)
                                item = page.locator("li", has_text=identifier).first
                                display = display_text_without_buttons(
                                    item.inner_text() or "",
                                    [t for t in item.get_by_role("button").all_inner_texts() if t],
                                )
                                if not status_shown_in_display(display, expected):
                                    raise AssertionError(f"status {expected!r} not shown after advance")
                                after_names = []
                                for after_btn in item.get_by_role("button", name=ADVANCE_BUTTON).all():
                                    try:
                                        after_names.append(
                                            (after_btn.get_attribute("aria-label")
                                             or after_btn.inner_text() or "").strip()
                                        )
                                    except Exception:
                                        pass
                                if not button_moved_past(after_names, expected):
                                    raise AssertionError(f"control still targets {expected!r} after advance")
                                chain.append(expected)
                            last_status = chain[-1]
                            rec("advance", "pass",
                                f"buttons: status advanced twice (… -> {' -> '.join(chain)}), display verified",
                                (timed() - t0) * 1000)
                    shot(page, "advanced")
        except AssertionError as exc:
            rec("advance", "fail", str(exc)[:500], (timed() - t0) * 1000)
        except Exception as exc:
            rec("advance", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- invalid: fresh Received item, attempt illegal skip (select apps only) ---
        t0 = timed()
        try:
            if identifier is None:
                rec("invalid", "blocked", "no order identifier available", (timed() - t0) * 1000)
            else:
                fresh_load()
                page.locator("h1").first.wait_for(state="visible")
                item = page.locator("li", has_text=identifier).first
                sel = item.locator("select").first if item.count() else None
                if sel is None or sel.count() == 0:
                    rec("invalid", "not_applicable", "no select control; buttons only offer valid transitions",
                        (timed() - t0) * 1000)
                else:
                    pairs = []
                    for opt in sel.locator("option").all():
                        label = opt.inner_text().strip()
                        pairs.append((((opt.get_attribute("value") or "") or label).strip(), label))
                    if len(pairs) < 3:
                        rec("invalid", "not_applicable",
                            "no skippable option; invalid transitions not expressible",
                            (timed() - t0) * 1000)
                    else:
                        initial_value, initial_label = pairs[0]
                        skip_value, skip_label = pairs[2]
                        target = identifier
                        if sel.input_value() != initial_value:
                            marker2 = MARKER + "-2"
                            form = page.locator("form").first
                            scope = form if form.count() else page
                            counts = fill_create_form(scope, marker2)
                            if counts["text"] == 0:
                                rec("invalid", "not_applicable", "cannot obtain a fresh initial-status item",
                                    (timed() - t0) * 1000)
                                target = None
                            else:
                                scope.get_by_role("button").first.click()
                                page.get_by_text(marker2).first.wait_for(state="visible")
                                target = marker2
                        if target is not None:
                            item = page.locator("li", has_text=target).first
                            sel = item.locator("select").first
                            sel.select_option(label=skip_label)
                            page.wait_for_timeout(400)
                            if sel.input_value() == initial_value:
                                notice = page.get_by_role("alert").count() > 0
                                rec("invalid", "pass",
                                    f"skip to {skip_label} rejected, stayed {initial_label} "
                                    f"(notice shown: {notice})",
                                    (timed() - t0) * 1000)
                            else:
                                rec("invalid", "fail",
                                    f"illegal skip accepted: status is {sel.input_value()!r}",
                                    (timed() - t0) * 1000)
        except Exception as exc:
            rec("invalid", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- persist: reload, identifier + last status must survive ---
        t0 = timed()
        try:
            if identifier is None:
                rec("persist", "blocked", "no order identifier available", (timed() - t0) * 1000)
            else:
                via = fresh_load()
                page.locator("h1").first.wait_for(state="visible")
                item = page.locator("li", has_text=identifier).first
                if item.count() == 0:
                    rec("persist", "fail", f"order {identifier!r} missing after reload", (timed() - t0) * 1000)
                else:
                    sel = item.locator("select").first
                    if sel.count() > 0:
                        ok = sel.input_value() == last_status
                    else:
                        display = display_text_without_buttons(
                            item.inner_text() or "",
                            [t for t in item.get_by_role("button").all_inner_texts() if t],
                        )
                        ok = status_shown_in_display(display, last_status)
                    if ok:
                        rec("persist", "pass", f"order + {last_status} status survived reload ({via})",
                            (timed() - t0) * 1000)
                    else:
                        rec("persist", "fail", f"order present but {last_status} status lost after reload",
                            (timed() - t0) * 1000)
                shot(page, "reloaded")
        except Exception as exc:
            rec("persist", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- responsive: mobile viewport loads without horizontal overflow ---
        t0 = timed()
        try:
            page.set_viewport_size({"width": 390, "height": 844})
            via = fresh_load()
            page.locator("h1").first.wait_for(state="visible")
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth"
                " - document.documentElement.clientWidth"
            )
            shot(page, "mobile")
            if overflow and overflow > 1:
                rec("responsive", "fail",
                    f"horizontal overflow at 390px (over by {overflow}px)",
                    (timed() - t0) * 1000)
            else:
                rec("responsive", "pass",
                    f"390px viewport loads with no horizontal overflow ({via})",
                    (timed() - t0) * 1000)
        except Exception as exc:
            rec("responsive", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- console: no runtime errors anywhere in the run ---
        t0 = timed()
        if console_errors:
            uniq = list(dict.fromkeys(console_errors))[:3]
            rec("console", "fail",
                f"{len(console_errors)} console error(s): {'; '.join(uniq)}"[:500],
                (timed() - t0) * 1000)
        else:
            rec("console", "pass",
                "no console errors or page errors during workflow",
                (timed() - t0) * 1000)

        browser.close()
        return {"browser": f"firefox {version}", "steps": steps, "screenshots": shots}


try:
    report = main()
except Exception as exc:  # never exit without a machine-readable report
    import traceback

    report = {"browser": "firefox (crashed)", "steps": [
        {"name": n, "status": "fail" if n == "load" else "blocked",
         "detail": f"runner crashed: {traceback.format_exc(limit=3)[-500:]}", "duration_ms": 0}
        for n in ("load", "create", "advance", "invalid", "persist",
                  "console", "responsive")], "screenshots": []}
print("BROWSER_JSON:" + json.dumps(report))
'''


def determine_required_steps(acceptance_criteria: list[str] | None) -> dict[str, bool]:
    """Which browser steps the spec demands (pure; everything else may skip)."""
    joined = " ".join(acceptance_criteria or []).lower()

    def mentions(keywords: tuple[str, ...]) -> bool:
        return any(kw in joined for kw in keywords)

    return {
        "load": True,
        "create": mentions(CREATE_KEYWORDS),
        "advance": mentions(ADVANCE_KEYWORDS),
        "invalid": mentions(INVALID_KEYWORDS),
        "persist": mentions(PERSIST_KEYWORDS),
        # Quality signals: executed and reported, never verdict-gating, so
        # re-verifying an older application cannot flip its verdict.
        "console": False,
        "responsive": False,
    }


def browser_steps_to_checks(
    report: BrowserTestResult, required: dict[str, bool]
) -> list[VerificationCheck]:
    """Map executed browser steps to checks. SKIP never counts as pass;
    required-but-missing workflows FAIL with the discovery reason."""
    by_name = {step.name: step for step in report.steps}
    checks: list[VerificationCheck] = []
    for step_name, check_name in STEP_CHECK_NAMES.items():
        step = by_name.get(step_name)
        is_required = required.get(step_name, step_name == "load")
        if step_name == "load":
            category = CheckCategory.RUNTIME
        elif step_name in ("console", "responsive"):
            category = CheckCategory.QUALITY
        else:
            category = CheckCategory.FUNCTIONAL
        if step is None:
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.FAIL,
                required=is_required,
                detail=f"Browser step {step_name!r} missing from report.",
            ))
            continue
        latency = round(step.duration_ms / 1000, 1)
        if step.status == BrowserStepStatus.PASS:
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.PASS,
                required=is_required, detail=f"Real browser: {step.detail}", latency_s=latency,
            ))
        elif step.status == BrowserStepStatus.NOT_APPLICABLE and (
            not is_required or step_name == "invalid"
        ):
            # Untestable invalid-transitions (buttons-only UI, no skippable
            # option) never fail the run: an unexpressible invalid path is
            # not a defect. Coverage still records the criterion UNVERIFIED.
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.SKIPPED,
                required=False, detail=f"Not executed: {step.detail}",
                latency_s=latency,
            ))
        elif step.status == BrowserStepStatus.NOT_APPLICABLE:
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.FAIL,
                required=True,
                detail=f"Required workflow missing in app: {step.detail}", latency_s=latency,
            ))
        elif step.status == BrowserStepStatus.BLOCKED and is_required:
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.FAIL,
                required=True, detail=f"Blocked, could not execute: {step.detail}",
                latency_s=latency,
            ))
        elif step.status == BrowserStepStatus.BLOCKED:
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.SKIPPED,
                required=False, detail=f"Blocked, could not execute: {step.detail}",
                latency_s=latency,
            ))
        else:
            if is_required:
                checks.append(VerificationCheck(
                    name=check_name, category=category, status=CheckStatus.FAIL,
                    required=True, detail=f"Real browser: {step.detail}", latency_s=latency,
                ))
            else:
                # Executed and failed, but not demanded by the acceptance
                # criteria: report FAIL honestly (it ran) while staying
                # non-gating (summarize_e2b counts required checks only).
                checks.append(VerificationCheck(
                    name=check_name, category=category, status=CheckStatus.FAIL,
                    required=False,
                    detail=f"Real browser: {step.detail} (not required by acceptance criteria)",
                    latency_s=latency,
                ))
    return checks


def unavailable_browser_checks(required: dict[str, bool], reason: str) -> list[VerificationCheck]:
    def category_for(step: str):
        if step == "load":
            return CheckCategory.RUNTIME
        if step in ("console", "responsive"):
            return CheckCategory.QUALITY
        return CheckCategory.FUNCTIONAL

    return [
        VerificationCheck(
            name=check_name,
            category=category_for(step),
            status=CheckStatus.UNAVAILABLE,
            required=required.get(step, step == "load"),
            detail=f"Browser unavailable: {reason}",
        )
        for step, check_name in STEP_CHECK_NAMES.items()
    ]


def run_browser_tests(sbx, base_url: str) -> BrowserTestResult:
    """Upload and run the FIELD-owned workflow script in the sandbox."""
    started = time.perf_counter()
    marker = f"BROWSER-{int(time.time())}"
    sbx.files.write(BROWSER_ASSERT_PATH, BROWSER_ASSERT_SOURCE)
    sbx.files.write(BROWSER_SCRIPT_PATH, BROWSER_SCRIPT)
    try:
        # Generous ceiling: page ops already time out individually at 60s;
        # this only bounds a wedged runner under very slow infrastructure.
        result = sbx.commands.run(
            f"python3 {BROWSER_SCRIPT_PATH} {base_url} {marker}",
            timeout=600,
        )
    except Exception as exc:  # noqa: BLE001 - SDK raises on timeout/non-zero
        return BrowserTestResult(
            available=False,
            error=f"Browser script did not complete: {type(exc).__name__}: {exc}"[:800],
            latency_s=round(time.perf_counter() - started, 1),
        )
    report = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("BROWSER_JSON:"):
            try:
                report = json.loads(line[len("BROWSER_JSON:"):])
            except ValueError:
                report = None
    if not isinstance(report, dict) or not isinstance(report.get("steps"), list):
        return BrowserTestResult(
            available=False,
            error=f"No machine-readable browser report. stdout tail: {(result.stdout or '')[-500:]}",
            latency_s=round(time.perf_counter() - started, 1),
        )
    steps: list[BrowserStep] = []
    for entry in report["steps"]:
        try:
            steps.append(BrowserStep(
                name=str(entry.get("name", "?")),
                status=BrowserStepStatus(str(entry.get("status", "fail"))),
                detail=str(entry.get("detail", ""))[:800],
                duration_ms=int(entry.get("duration_ms", 0) or 0),
            ))
        except (ValueError, TypeError, AttributeError):
            steps.append(BrowserStep(name="?", status=BrowserStepStatus.FAIL,
                                     detail="Malformed step entry in browser report."))
    screenshots: list[BrowserScreenshot] = []
    for shot in report.get("screenshots", []) or []:
        name = str(shot.get("name", "?"))
        path = str(shot.get("path", ""))
        data_url = ""
        if path:
            try:
                raw = bytes(sbx.files.read(path, format="bytes"))
                if raw:
                    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
            except Exception:  # noqa: BLE001 - screenshot loss must not fail the run
                data_url = ""
        screenshots.append(BrowserScreenshot(name=name, data_url=data_url))
    return BrowserTestResult(
        available=True,
        browser=str(report.get("browser", ""))[:100],
        steps=steps,
        screenshots=screenshots,
        latency_s=round(time.perf_counter() - started, 1),
    )
