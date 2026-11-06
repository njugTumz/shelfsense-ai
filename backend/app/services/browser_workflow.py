"""Real browser interaction testing inside the E2B sandbox.

FIELD owns the test runner (Muse Spark never generates it). The script
below drives headless Firefox against the in-sandbox preview over
localhost, discovers controls by accessible roles with fallbacks, and
reports per-step JSON. The backend maps steps to verification checks:
required-by-spec workflows that are missing or broken FAIL; workflows
the spec never asks for SKIP (never pass).

Workflow ``restaurant``: load -> create -> advance x2 -> storage ->
invalid -> reload/persist -> responsive -> console. The storage step
dumps browser web storage right after the advance and asserts the
stored order holds the reached status, independent of DOM selectors.
The console and responsive steps are quality signals: they report
honestly but never gate the verdict, so older applications keep their
behavior on re-verification. Other workflows can be added as new
scripts; the report/check mapping stays shared.
"""

from __future__ import annotations

import base64
import json
import re
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
#: Ceiling for one browser-script invocation. The walker alone measured
#: 341s on the c46d retry (577s browser total); 900s keeps headroom while
#: staying inside the 1200s sandbox cap. Sandbox cap and all other
#: timeouts are unchanged.
BROWSER_SCRIPT_TIMEOUT_S = 900
#: JSONL progress the template appends per recorded step (mirrored on
#: stdout as BROWSER_STEP lines). MUST match PROGRESS_PATH in
#: BROWSER_SCRIPT. Read back after a budget timeout so a killed run
#: still returns the steps it completed.
BROWSER_PROGRESS_PATH = "/tmp/browser_progress.jsonl"

CREATE_KEYWORDS = ("create", "add", "new order", "entry", "submit", "form")
ADVANCE_KEYWORDS = ("status", "transition", "preparing", "ready", "move", "advance", "update")
INVALID_KEYWORDS = ("invalid", "reject")
PERSIST_KEYWORDS = ("persist", "reload", "refresh", "storage", "survive")

STEP_CHECK_NAMES = {
    "load": "browser_loads_app",
    "create": "browser_creates_order",
    "advance": "browser_advances_status",
    "storage": "browser_storage_holds_status",
    "invalid": "browser_rejects_invalid",
    "persist": "browser_persists_after_reload",
    "walk": "browser_walks_full_lifecycle",
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
import json
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


def item_text_without_buttons(item):
    """Locator text with its button labels removed. Shared by walk_status
    and verify_hop so status reads never leak control labels (JSX strips
    inter-element newlines, gluing e.g. 'Ready' + 'Advance to ...')."""
    buttons = [t for t in item.get_by_role("button").all_inner_texts() if t]
    return display_text_without_buttons(item.inner_text() or "", buttons)


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


def order_items(page, marker):
    """List items for exactly one order, across every rendered view.

    Matches ``li`` elements containing a child whose whole text is
    ``marker``. Plain substring matching also matches suffixed sibling
    orders (MARKER-2 contains MARKER) and lets DOM order pick the wrong
    one; a lookahead cannot fix it because Playwright joins element
    texts without separators when testing ``has_text``.
    """
    return page.locator("li", has=page.get_by_text(marker, exact=True))


def first_with_select(candidates):
    """First candidate containing a <select> control; None when absent.

    Multi-view apps render one order in several lists (queue + board) and
    DOM order decides which rendering `.first` returns, so select-based
    steps must pick the rendering that actually has the control instead
    of assuming it. Accepts any sequence of locator-likes.
    """
    for candidate in candidates or []:
        try:
            if candidate.locator("select").count() > 0:
                return candidate
        except Exception:
            continue
    return None


def skip_option_state(options, skip_value):
    """Expressibility of an illegal-skip attempt: absent|disabled|selectable.

    ``options`` is a list of (value_or_text, disabled) for the fresh
    control. 'absent'/'disabled' mean the UI never offers the invalid
    path (correct restrictive design); only 'selectable' attempts the
    skip so the app's rejection gate gets exercised.
    """
    seen = False
    for key, disabled in options or []:
        if key == skip_value:
            if not disabled:
                return "selectable"
            seen = True
    return "disabled" if seen else "absent"


def stored_statuses_for(dump, identifier):
    """Statuses at which ``identifier`` is stored in a web-storage dump.

    ``dump`` maps storage keys to raw string values. Walks every JSON
    value, attributing each exact identifier occurrence to its nearest
    enclosing string "status". Non-JSON values are skipped: without
    structure a status cannot be honestly attributed. Returns the list
    of attributed statuses (None entries for unattributed hits).
    """
    found = []
    if not identifier or not isinstance(dump, dict):
        return found

    def visit(node, status):
        if isinstance(node, dict):
            current = node.get("status")
            if not isinstance(current, str):
                current = status
            for value in node.values():
                visit(value, current)
        elif isinstance(node, list):
            for value in node:
                visit(value, status)
        elif isinstance(node, str) and node == identifier:
            found.append(status)

    for raw in dump.values():
        if not isinstance(raw, str):
            continue
        try:
            visit(json.loads(raw), None)
        except Exception:
            continue
    return found


def first_with_advance_button(candidates):
    """First candidate containing an advance control; None when absent.

    Sibling of first_with_select for button-driven renderings: a
    candidate qualifies when any of its buttons parses to a status
    target. Accepts any sequence of locator-likes.
    """
    for candidate in candidates or []:
        try:
            buttons = candidate.get_by_role("button").all()
        except Exception:
            continue
        for button in buttons:
            try:
                name = button.inner_text() or ""
            except Exception:
                continue
            if parse_button_target(name) is not None:
                return candidate
    return None


def next_forward_option(options, current):
    """Next lifecycle hop from a select's options; None at terminal.

    ``options`` is a [(value_or_text, disabled)] list in DOM order.
    Returns the first enabled option positioned after ``current``,
    never Cancelled (destructive) and never backward (loops). An
    unrecognized current status also yields None: stop, don't guess.
    """
    seen_current = False
    for key, disabled in options or []:
        if not seen_current:
            if key == current:
                seen_current = True
            continue
        if disabled:
            continue
        if key == "Cancelled":
            continue
        return key
    return None


def read_display_status(display_text):
    """Status word from 'Status: X' display text; None when absent."""
    match = re.search(r"Status:\s*([A-Za-z]+)", display_text or "")
    return match.group(1) if match else None
'''

BROWSER_SCRIPT = r'''
"""FIELD-owned restaurant workflow test. argv: url marker [deadline_s]. Prints BROWSER_JSON line."""
import json
import re
import sys
import time

from browser_assert import (
    button_moved_past,
    display_text_without_buttons,
    first_with_advance_button,
    first_with_select,
    item_text_without_buttons,
    next_forward_option,
    order_items,
    parse_button_target,
    read_display_status,
    skip_option_state,
    status_shown_in_display,
    stored_statuses_for,
)

URL, MARKER = sys.argv[1], sys.argv[2]
# Internal global deadline: stop starting new work this far into the run
# and record the rest as not_run, so a slow run returns a full report
# instead of dying at the 900s invocation budget with zero evidence.
# Overridable via argv for tests; production always uses the default.
INTERNAL_DEADLINE_S = float(sys.argv[3]) if len(sys.argv) > 3 else 780.0


class _DeadlineExceeded(Exception):
    """Raised to skip a step section once the internal deadline passes."""
    pass


def _deadline_detail():
    return (f"internal {INTERNAL_DEADLINE_S:g}s deadline exceeded; "
            "step not attempted")
# Every recorded step is appended here (one JSON object per line) AND
# echoed as a BROWSER_STEP stdout line, so a budget kill that discards
# the final report still leaves retrievable partial evidence behind.
# MUST match BROWSER_PROGRESS_PATH in the Python driver below.
PROGRESS_PATH = "/tmp/browser_progress.jsonl"
try:
    open(PROGRESS_PATH, "w").close()
except Exception:
    pass
ADVANCE_BUTTON = re.compile("advance|next|move|mark|progress|forward", re.I)
SUBMIT_BUTTON = re.compile("add|create|submit|save|new order", re.I)


def main():
    from playwright.sync_api import sync_playwright

    steps = []
    shots = []
    script_t0 = time.time()

    def deadline_hit():
        return time.time() - script_t0 > INTERNAL_DEADLINE_S

    def rec(name, status, detail="", ms=0, chain=None, hops=None):
        entry = {"name": name, "status": status, "detail": detail[:800],
                 "duration_ms": int(ms), "chain": list(chain or []),
                 "hop_durations_ms": [int(h) for h in (hops or [])]}
        steps.append(entry)
        try:
            with open(PROGRESS_PATH, "a") as progress_file:
                progress_file.write(json.dumps(entry) + "\n")
        except Exception:
            pass
        try:
            print("BROWSER_STEP:" + json.dumps(entry), flush=True)
        except Exception:
            pass

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
            if deadline_hit():
                raise _DeadlineExceeded()
            t0 = timed()
            page.goto(URL, wait_until="load")
            page.locator("h1").first.wait_for(state="visible")
            heading = page.locator("h1").first.inner_text()[:100]
            rec("load", "pass", f"loaded; heading: {heading}", (timed() - t0) * 1000)
        except _DeadlineExceeded:
            rec("load", "not_run", _deadline_detail(), 0)
            for name in ("create", "advance", "storage", "invalid", "persist", "walk", "console", "responsive"):
                rec(name, "not_run", _deadline_detail())
            return {"browser": f"firefox {version}", "steps": steps, "screenshots": shots,
                    "deadline_exceeded": True}
        except Exception as exc:
            rec("load", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)
            for name in ("create", "advance", "storage", "invalid", "persist", "walk", "console", "responsive"):
                rec(name, "blocked", "page did not load")
            return {"browser": f"firefox {version}", "steps": steps, "screenshots": shots,
                    "deadline_exceeded": any(s["status"] == "not_run" for s in steps)}

        identifier = None
        last_status = "Received"

        # --- create ---
        t0 = timed()
        try:
            if deadline_hit():
                raise _DeadlineExceeded()
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
        except _DeadlineExceeded:
            rec("create", "not_run", _deadline_detail(), (timed() - t0) * 1000)
        except Exception as exc:
            shot(page, "create_failed")
            rec("create", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- advance Received -> Preparing -> Ready ---
        t0 = timed()
        try:
            if deadline_hit():
                raise _DeadlineExceeded()
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
                item = order_items(page, identifier).first
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
                                item = order_items(page, identifier).first
                                btn = item.get_by_role("button", name=ADVANCE_BUTTON).first
                                if btn.count() == 0:
                                    raise AssertionError("no advance button for next transition")
                                name = (btn.get_attribute("aria-label") or btn.inner_text() or "").strip()
                                expected = parse_button_target(name)
                                if not expected:
                                    raise AssertionError(f"advance button does not state next status: {name!r}")
                                btn.click()
                                page.wait_for_timeout(400)
                                item = order_items(page, identifier).first
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
        except _DeadlineExceeded:
            rec("advance", "not_run", _deadline_detail(), (timed() - t0) * 1000)
        except Exception as exc:
            rec("advance", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- storage: dumped web storage must hold the reached status ---
        t0 = timed()
        try:
            if deadline_hit():
                raise _DeadlineExceeded()
            if identifier is None:
                rec("storage", "blocked", "no order identifier available", (timed() - t0) * 1000)
            elif last_status == "Received":
                rec("storage", "not_applicable", "no status transition to verify in storage",
                    (timed() - t0) * 1000)
            else:
                dump = page.evaluate("""() => {
                    const out = {};
                    for (const area of [window.localStorage, window.sessionStorage]) {
                        try {
                            for (let i = 0; i < area.length; i++) {
                                const k = area.key(i);
                                try { out[k] = area.getItem(k); } catch (e) { out[k] = "<unreadable>"; }
                            }
                        } catch (e) { /* storage area unavailable */ }
                    }
                    return out;
                }""")
                if not dump:
                    rec("storage", "not_applicable", "no web-storage entries dumped",
                        (timed() - t0) * 1000)
                else:
                    found = stored_statuses_for(dump, identifier)
                    if last_status in found:
                        rec("storage", "pass",
                            f"storage holds {identifier} at {last_status} ({len(dump)} keys dumped)",
                            (timed() - t0) * 1000)
                    else:
                        seen = sorted({s for s in found if s is not None}) or ["identifier absent"]
                        rec("storage", "fail",
                            f"stored {identifier} at {', '.join(seen)}; expected {last_status}",
                            (timed() - t0) * 1000)
        except _DeadlineExceeded:
            rec("storage", "not_run", _deadline_detail(), (timed() - t0) * 1000)
        except Exception as exc:
            rec("storage", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- invalid: fresh Received item, attempt illegal skip (select apps only) ---
        t0 = timed()
        try:
            if deadline_hit():
                raise _DeadlineExceeded()
            if identifier is None:
                rec("invalid", "blocked", "no order identifier available", (timed() - t0) * 1000)
            else:
                fresh_load()
                page.locator("h1").first.wait_for(state="visible")
                item = first_with_select(order_items(page, identifier).all())
                sel = item.locator("select").first if item is not None else None
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
                            item = first_with_select(order_items(page, target).all())
                            if item is None:
                                rec("invalid", "not_applicable",
                                    "fresh item has no select control in any view",
                                    (timed() - t0) * 1000)
                            else:
                                sel = item.locator("select").first
                                fresh_opts = []
                                for opt in sel.locator("option").all():
                                    text = (opt.inner_text() or "").strip()
                                    key = ((opt.get_attribute("value") or "") or text).strip()
                                    try:
                                        dis = bool(opt.is_disabled())
                                    except Exception:
                                        dis = False
                                    fresh_opts.append((key, dis))
                                state = skip_option_state(fresh_opts, skip_value)
                                if state == "absent":
                                    rec("invalid", "not_applicable",
                                        f"skip to {skip_label} not offered; invalid path not expressible",
                                        (timed() - t0) * 1000)
                                elif state == "disabled":
                                    rec("invalid", "not_applicable",
                                        f"skip to {skip_label} disabled; UI only offers valid transitions",
                                        (timed() - t0) * 1000)
                                else:
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
        except _DeadlineExceeded:
            rec("invalid", "not_run", _deadline_detail(), (timed() - t0) * 1000)
        except Exception as exc:
            rec("invalid", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- persist: reload, identifier + last status must survive ---
        t0 = timed()
        try:
            if deadline_hit():
                raise _DeadlineExceeded()
            if identifier is None:
                rec("persist", "blocked", "no order identifier available", (timed() - t0) * 1000)
            else:
                via = fresh_load()
                page.locator("h1").first.wait_for(state="visible")
                item = order_items(page, identifier).first
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
        except _DeadlineExceeded:
            rec("persist", "not_run", _deadline_detail(), (timed() - t0) * 1000)
        except Exception as exc:
            rec("persist", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- walk: advance through every reachable status to terminal ---
        t0 = timed()
        try:
            if deadline_hit():
                raise _DeadlineExceeded()
            if identifier is None:
                rec("walk", "blocked", "no order identifier available", (timed() - t0) * 1000)
            else:
                hop_marks = []
                load_t0 = timed()
                walk_via = fresh_load()
                page.locator("h1").first.wait_for(state="visible")
                load_ms = (timed() - load_t0) * 1000

                def walk_status(item):
                    sel = item.locator("select").first
                    if sel.count() > 0:
                        return sel.input_value() or None
                    return read_display_status(item_text_without_buttons(item))

                def verify_hop(target):
                    found = order_items(page, identifier).all()
                    if not found:
                        return ("gone", None)
                    current = first_with_select(found)
                    if current is None:
                        current = first_with_advance_button(found)
                    if current is None:
                        current = found[0]
                    sel = current.locator("select").first
                    if sel.count() > 0:
                        value = sel.input_value() or None
                        return ("ok" if value == target else "wrong", value)
                    display = item_text_without_buttons(current)
                    if status_shown_in_display(display, target):
                        return ("ok", target)
                    return ("wrong", read_display_status(display))

                chain = []
                synthetic = []
                start_items = order_items(page, identifier).all()
                if not start_items:
                    rec("walk", "fail", f"order {identifier!r} missing, cannot walk",
                        (timed() - t0) * 1000, chain=chain)
                else:
                    start = walk_status(start_items[0])
                    if start:
                        chain.append(start)
                    outcome = None
                    deadline_cut_walk = False

                    def hop_diag():
                        if not hop_marks:
                            return ""
                        segs = "; ".join(
                            f"{label} {ms / 1000:.1f}s" for label, ms in hop_marks)
                        return f" (hops: {segs})"

                    def hop_ms():
                        return [ms for _, ms in hop_marks]

                    for _hop in range(8):
                        if deadline_hit():
                            outcome = ("internal deadline exceeded; "
                                       f"walked {' -> '.join(chain)}" + hop_diag())
                            deadline_cut_walk = True
                            break
                        candidates = order_items(page, identifier).all()
                        item = first_with_select(candidates)
                        if item is None:
                            item = first_with_advance_button(candidates)
                        if item is None:
                            outcome = "terminal: no advancable control"
                            break
                        sel = item.locator("select").first
                        if sel.count() > 0:
                            opts = []
                            for opt in sel.locator("option").all():
                                text = (opt.inner_text() or "").strip()
                                key = ((opt.get_attribute("value") or "") or text).strip()
                                try:
                                    dis = bool(opt.is_disabled())
                                except Exception:
                                    dis = False
                                opts.append((key, dis))
                            current = sel.input_value() or None
                            nxt = next_forward_option(opts, current)
                            if nxt is None:
                                outcome = f"terminal at {current}"
                                break
                            if nxt in chain:
                                outcome = f"cycle detected at {nxt}"
                                break
                            hop_t0 = timed()
                            sel.select_option(value=nxt)
                            page.wait_for_timeout(400)
                            result, now = verify_hop(nxt)
                            hop_marks.append((
                                f"{chain[-1] if chain else '?'}->{nxt}",
                                (timed() - hop_t0) * 1000))
                            if result == "ok":
                                chain.append(nxt)
                            elif result == "gone":
                                outcome = (f"order left all lists after hop to {nxt}; "
                                           "chain ends at last verified status")
                                break
                            else:
                                rec("walk", "fail",
                                    f"hop to {nxt} not shown (at {now}); "
                                    f"walked {' -> '.join(chain)}" + hop_diag(),
                                    (timed() - t0) * 1000, chain=chain,
                                    hops=hop_ms())
                                break
                        else:
                            btn = item.get_by_role("button", name=ADVANCE_BUTTON).first
                            if btn.count() == 0:
                                outcome = f"terminal at {chain[-1] if chain else 'unknown'}"
                                break
                            target = parse_button_target(btn.inner_text() or "")
                            if target is None:
                                outcome = "terminal: advance control has no parseable target"
                                break
                            if target in chain:
                                outcome = f"cycle detected at {target}"
                                break
                            try:
                                enabled = btn.is_enabled()
                            except Exception:
                                enabled = True
                            if not enabled:
                                filled = False
                                fields = item.locator(
                                    'input[type="text"], input[type="number"], '
                                    'input:not([type]), textarea').all()
                                for field in fields:
                                    try:
                                        if field.is_visible() and (field.input_value() or "") == "":
                                            field.fill("1")
                                            filled = True
                                    except Exception:
                                        continue
                                if filled:
                                    synthetic.append(chain[-1] if chain else "?")
                                    page.wait_for_timeout(400)
                                try:
                                    enabled = btn.is_enabled()
                                except Exception:
                                    enabled = True
                            if not enabled:
                                outcome = f"input-gated at {chain[-1] if chain else 'unknown'}"
                                break
                            hop_t0 = timed()
                            btn.click()
                            page.wait_for_timeout(400)
                            result, now = verify_hop(target)
                            hop_marks.append((
                                f"{chain[-1] if chain else '?'}->{target}",
                                (timed() - hop_t0) * 1000))
                            if result == "ok":
                                chain.append(target)
                            elif result == "gone":
                                outcome = (f"order left all lists after hop to {target}; "
                                           "chain ends at last verified status")
                                break
                            else:
                                rec("walk", "fail",
                                    f"hop to {target} not shown (at {now}); "
                                    f"walked {' -> '.join(chain)}" + hop_diag(),
                                    (timed() - t0) * 1000, chain=chain,
                                    hops=hop_ms())
                                break
                    else:
                        outcome = "hop cap reached"
                    if outcome is not None:
                        walked = " -> ".join(chain) if chain else "(no hops)"
                        detail = f"walked {walked} [{outcome}]"
                        detail += f" (loaded via {walk_via} in {load_ms / 1000:.1f}s)"
                        detail += hop_diag()
                        if synthetic:
                            detail += f" (synthetic input filled at: {', '.join(synthetic)})"
                        walk_status = "not_run" if deadline_cut_walk else "pass"
                        rec("walk", walk_status, detail, (timed() - t0) * 1000, chain=chain,
                            hops=hop_ms())
        except _DeadlineExceeded:
            rec("walk", "not_run", _deadline_detail(), (timed() - t0) * 1000)
        except Exception as exc:
            rec("walk", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- responsive: mobile viewport loads without horizontal overflow ---
        t0 = timed()
        try:
            if deadline_hit():
                raise _DeadlineExceeded()
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
        except _DeadlineExceeded:
            rec("responsive", "not_run", _deadline_detail(), (timed() - t0) * 1000)
        except Exception as exc:
            rec("responsive", "fail", f"{type(exc).__name__}: {exc}"[:500], (timed() - t0) * 1000)

        # --- console: no runtime errors anywhere in the run ---
        t0 = timed()
        if deadline_hit():
            rec("console", "not_run", _deadline_detail(), (timed() - t0) * 1000)
        elif console_errors:
            uniq = list(dict.fromkeys(console_errors))[:3]
            rec("console", "fail",
                f"{len(console_errors)} console error(s): {'; '.join(uniq)}"[:500],
                (timed() - t0) * 1000)
        else:
            rec("console", "pass",
                "no console errors or page errors during workflow",
                (timed() - t0) * 1000)

        browser.close()
        return {"browser": f"firefox {version}", "steps": steps, "screenshots": shots,
                "deadline_exceeded": any(s["status"] == "not_run" for s in steps)}


try:
    report = main()
except Exception as exc:  # never exit without a machine-readable report
    import traceback

    report = {"browser": "firefox (crashed)", "steps": [
        {"name": n, "status": "fail" if n == "load" else "blocked",
         "detail": f"runner crashed: {traceback.format_exc(limit=3)[-500:]}", "duration_ms": 0}
        for n in ("load", "create", "advance", "storage", "invalid", "persist", "walk",
                  "console", "responsive")], "screenshots": [],
        "deadline_exceeded": False}
print("BROWSER_JSON:" + json.dumps(report))
'''


# A reload/refresh under direct negation ("updates without page reload")
# describes live updates, not a persistence demand. Only these tight
# adjacent forms are discounted before the persist keyword scan: anything
# else ("without data loss on reload", "no orders lost on reload") keeps
# the gate armed, since an unneeded test run is safe but a silently
# dropped genuine requirement would weaken verification.
NEGATED_RELOAD_REFRESH = re.compile(
    r"\bwithout\s+(?:a\s+|the\s+|any\s+|manual\s+)?(?:page\s+)?(?:reload|refresh(?:ing)?)\b"
    r"|\bno\s+(?:page\s+)?(?:reload|refresh)\b"
)


def determine_required_steps(acceptance_criteria: list[str] | None) -> dict[str, bool]:
    """Which browser steps the spec demands (pure; everything else may skip)."""
    joined = " ".join(acceptance_criteria or []).lower()
    persist_text = NEGATED_RELOAD_REFRESH.sub(" ", joined)

    def mentions(keywords: tuple[str, ...]) -> bool:
        return any(kw in joined for kw in keywords)

    return {
        "load": True,
        "create": mentions(CREATE_KEYWORDS),
        "advance": mentions(ADVANCE_KEYWORDS),
        # The lifecycle walk is the transition evidence at full range:
        # required exactly when the advance gate is.
        "walk": mentions(ADVANCE_KEYWORDS),
        # Storage dump is mechanism-specific (web storage only): an app
        # persisting via IndexedDB or a backend would fail it while
        # working, so it reports honestly but never gates the verdict.
        "storage": False,
        "invalid": mentions(INVALID_KEYWORDS),
        "persist": any(kw in persist_text for kw in PERSIST_KEYWORDS),
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
                required=is_required, detail=f"Real browser: {step.detail}", latency_s=latency, chain=list(step.chain or []), hop_durations_ms=list(step.hop_durations_ms or []),
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
                latency_s=latency, chain=list(step.chain or []), hop_durations_ms=list(step.hop_durations_ms or []),
            ))
        elif step.status == BrowserStepStatus.NOT_APPLICABLE:
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.FAIL,
                required=True,
                detail=f"Required workflow missing in app: {step.detail}", latency_s=latency, chain=list(step.chain or []), hop_durations_ms=list(step.hop_durations_ms or []),
            ))
        elif step.status == BrowserStepStatus.BLOCKED and is_required:
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.FAIL,
                required=True, detail=f"Blocked, could not execute: {step.detail}",
                latency_s=latency, chain=list(step.chain or []), hop_durations_ms=list(step.hop_durations_ms or []),
            ))
        elif step.status == BrowserStepStatus.BLOCKED:
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.SKIPPED,
                required=False, detail=f"Blocked, could not execute: {step.detail}",
                latency_s=latency, chain=list(step.chain or []), hop_durations_ms=list(step.hop_durations_ms or []),
            ))
        elif step.status == BrowserStepStatus.NOT_RUN:
            # Unmeasured, never failed: the deadline fired first. The
            # required flag is kept (it WAS required) so coverage and
            # the verdict treat it as missing evidence, not a pass.
            checks.append(VerificationCheck(
                name=check_name, category=category, status=CheckStatus.UNAVAILABLE,
                required=is_required, detail=f"Not run: {step.detail}",
                latency_s=latency, chain=list(step.chain or []), hop_durations_ms=list(step.hop_durations_ms or []),
            ))
        else:
            if is_required:
                checks.append(VerificationCheck(
                    name=check_name, category=category, status=CheckStatus.FAIL,
                    required=True, detail=f"Real browser: {step.detail}", latency_s=latency, chain=list(step.chain or []), hop_durations_ms=list(step.hop_durations_ms or []),
                ))
            else:
                # Executed and failed, but not demanded by the acceptance
                # criteria: report FAIL honestly (it ran) while staying
                # non-gating (summarize_e2b counts required checks only).
                checks.append(VerificationCheck(
                    name=check_name, category=category, status=CheckStatus.FAIL,
                    required=False,
                    detail=f"Real browser: {step.detail} (not required by acceptance criteria)",
                    latency_s=latency, chain=list(step.chain or []), hop_durations_ms=list(step.hop_durations_ms or []),
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


#: E2B SDK failures that mean the sandbox (not the app) broke contact.
#: CommandExitException is deliberately absent: a command that ran and
#: exited non-zero is measured evidence, never a transport failure.
_E2B_TRANSIENT_ERROR_NAMES = (
    "SandboxUnreachableException",
    "SandboxNotRunningException",
    "SandboxNotFoundException",
    "TimeoutException",
    "RateLimitException",
    "ServiceBusyException",
    "FileUploadException",
)


def _is_transport_error(exc: BaseException) -> bool:
    """Whether the failure is sandbox transport rather than the app.

    Type-based only: httpx transport errors plus the transient E2B SDK
    errors above. Never message-sniffed, so app failures cannot be
    misclassified by wording.
    """
    import httpx

    if isinstance(exc, httpx.TransportError):
        return True
    try:
        import e2b
    except ImportError:
        return False
    transient = tuple(
        getattr(e2b, name, ()) for name in _E2B_TRANSIENT_ERROR_NAMES
    )
    return isinstance(exc, transient)


class _ScriptBudgetTimeout(Exception):
    """Marker: the script invocation hit its own time budget.

    Deliberately outside the transport taxonomy: a run that needs more
    than the budget would need it again, so retrying is futile (and a
    timed-out run may still be alive server-side, making a concurrent
    second run dangerous in a 473MB sandbox). Surfaces as INCONCLUSIVE
    with timeout copy, never retried.
    """


def _is_script_budget_timeout(exc: BaseException) -> bool:
    """Exact-base e2b TimeoutException (call-level timeout, not sandbox state).

    The SDK raises base TimeoutException for deadline_exceeded on the
    call itself; the SandboxNot*/SandboxUnreachable subclasses name
    sandbox-state failures and keep transport handling.
    """
    cls = type(exc)
    return (
        cls.__name__ == "TimeoutException"
        and cls.__module__.split(".")[0] == "e2b"
    )


class _PollingTimeout(Exception):
    """Marker: SDK polling timed out with unknown remote state.

    Deliberately outside the transport retry: the command may still be
    running server-side, so a second concurrent run risks OOM and
    cross-talk. Surfaces as INCONCLUSIVE with timeout copy, never
    retried. Checked before the transport taxonomy, which would
    otherwise claim httpx timeouts (TimeoutException subclasses
    TransportError in httpx 0.28).
    """


def _is_polling_timeout(exc: BaseException) -> bool:
    """Any httpx timeout: the poll never answered, remote state unknown."""
    import httpx

    return isinstance(exc, httpx.TimeoutException)


def _with_transport_retry(fn, delay_s: float = 10.0, sleep=time.sleep):
    """Run fn, retrying a single time after transport errors.

    Returns (value, retries_consumed, terminal_error): value is None
    when both attempts failed, in which case terminal_error is the last
    exception. Non-transport exceptions end the attempts immediately
    without consuming the retry. Never raises itself, so callers keep
    exact retry accounting on every path.
    """
    try:
        return fn(), 0, None
    except Exception as first:  # noqa: BLE001 - classified below
        if not _is_transport_error(first):
            return None, 0, first
        sleep(delay_s)
        try:
            return fn(), 1, None
        except Exception as second:  # noqa: BLE001 - returned, not raised
            return None, 1, second


def _browser_step_from_entry(entry: object) -> BrowserStep:
    """One report/progress entry into a BrowserStep (malformed -> FAIL)."""
    if not isinstance(entry, dict):
        return BrowserStep(name="?", status=BrowserStepStatus.FAIL,
                           detail="Malformed step entry in browser report.")
    try:
        raw_chain = entry.get("chain", [])
        if isinstance(raw_chain, list):
            chain = [s for s in raw_chain if isinstance(s, str)]
        else:
            chain = []
        raw_hops = entry.get("hop_durations_ms", [])
        if isinstance(raw_hops, list):
            hops = [int(h) for h in raw_hops
                    if isinstance(h, (int, float)) and not isinstance(h, bool)]
        else:
            hops = []
        return BrowserStep(
            name=str(entry.get("name", "?")),
            status=BrowserStepStatus(str(entry.get("status", "fail"))),
            detail=str(entry.get("detail", ""))[:800],
            duration_ms=int(entry.get("duration_ms", 0) or 0),
            chain=chain,
            hop_durations_ms=hops,
        )
    except (ValueError, TypeError, AttributeError):
        return BrowserStep(name="?", status=BrowserStepStatus.FAIL,
                           detail="Malformed step entry in browser report.")


def _read_progress_steps(sbx) -> list[BrowserStep]:
    """Steps the template flushed before a budget kill (best-effort).

    A torn final line (killed mid-write) is skipped, never fatal: a
    partial line is not evidence. Any read failure yields no steps.
    """
    try:
        raw = sbx.files.read(BROWSER_PROGRESS_PATH)
    except Exception:  # noqa: BLE001 - missing/unreachable file, no partials
        return []
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - undecodable, no partials
            return []
    elif isinstance(raw, str):
        text = raw
    else:
        return []
    steps: list[BrowserStep] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        step = _browser_step_from_entry(entry)
        if step.name != "?" or step.detail != "Malformed step entry in browser report.":
            steps.append(step)
    return steps


def run_browser_tests(sbx, base_url: str) -> BrowserTestResult:
    """Upload and run the FIELD-owned workflow script in the sandbox.

    Never retried at this level: a mid-stream transport error can leave
    the first run alive server-side, so a retry could overlap it (OOM
    and screenshot cross-talk in a 473MB sandbox). Transport failures
    go straight to INCONCLUSIVE with transport_retries=0; retry lives
    only at sandbox-create and file-upload, where nothing runs yet.
    A budget timeout additionally retrieves the steps the template
    flushed to its progress file, so the verdict carries evidence.
    """
    started = time.perf_counter()
    sbx.files.write(BROWSER_ASSERT_PATH, BROWSER_ASSERT_SOURCE)
    sbx.files.write(BROWSER_SCRIPT_PATH, BROWSER_SCRIPT)

    def invoke():
        # One attempt only (see docstring): a single time-based marker.
        marker = f"BROWSER-{int(time.time())}"
        # Generous ceiling: page ops already time out individually at 60s;
        # this only bounds a wedged runner under very slow infrastructure.
        try:
            return sbx.commands.run(
                f"python3 {BROWSER_SCRIPT_PATH} {base_url} {marker}",
                timeout=BROWSER_SCRIPT_TIMEOUT_S,
            )
        except Exception as exc:
            if _is_script_budget_timeout(exc):
                raise _ScriptBudgetTimeout(
                    f"verification timed out after {BROWSER_SCRIPT_TIMEOUT_S}s "
                    f"(budget exhausted; not retried): {exc}"
                ) from exc
            if _is_polling_timeout(exc):
                raise _PollingTimeout(
                    f"polling the sandbox timed out (status unknown; "
                    f"not retried): {exc}"
                ) from exc
            raise

    try:
        result = invoke()
    except Exception as exc:  # noqa: BLE001 - classified below, never retried
        partial: list[BrowserStep] = []
        timed_out = False
        if isinstance(exc, _ScriptBudgetTimeout):
            # The kill discards the final report, but flushed steps
            # survive in the progress file: retrieve them as evidence.
            # A burned budget is its own reason (script_timeout), not an
            # infrastructure failure: slowness is not breakage.
            partial = _read_progress_steps(sbx)
            error = f"{exc}"[:800]
            if partial:
                error = (
                    f"{len(partial)} of {len(STEP_CHECK_NAMES)} steps recorded "
                    f"before the budget fired. {error}"
                )[:800]
            timed_out, transport = True, False
        elif isinstance(exc, _PollingTimeout):
            error, transport = f"{exc}"[:800], True
        elif _is_transport_error(exc):
            error = (
                f"Browser script did not complete: {type(exc).__name__}: {exc} "
                "(transport error, not retried; re-run verification)"
            )[:800]
            transport = True
        else:
            error = f"Browser script did not complete: {type(exc).__name__}: {exc}"[:800]
            transport = False
        return BrowserTestResult(
            available=False,
            error=error,
            steps=partial,
            latency_s=round(time.perf_counter() - started, 1),
            transport_failed=transport,
            transport_retries=0,
            script_timeout=timed_out,
        )
    report = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("BROWSER_JSON:"):
            try:
                report = json.loads(line[len("BROWSER_JSON:"):])
            except ValueError:
                report = None
    if not isinstance(report, dict) or not isinstance(report.get("steps"), list):
        # The script ran but produced no report: measured (app-side),
        # never transport. Any consumed retry is still recorded.
        return BrowserTestResult(
            available=False,
            error=f"No machine-readable browser report. stdout tail: {(result.stdout or '')[-500:]}",
            latency_s=round(time.perf_counter() - started, 1),
            transport_retries=0,
        )
    steps = [_browser_step_from_entry(entry) for entry in report["steps"]]
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
    timed_out = report.get("deadline_exceeded") is True or any(
        s.status == BrowserStepStatus.NOT_RUN for s in steps)
    return BrowserTestResult(
        available=True,
        browser=str(report.get("browser", ""))[:100],
        steps=steps,
        screenshots=screenshots,
        latency_s=round(time.perf_counter() - started, 1),
        transport_retries=0,
        script_timeout=timed_out,
    )
