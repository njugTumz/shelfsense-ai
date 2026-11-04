"""Live browser-assertion tests: real Firefox in E2B against static fixtures.

Opt-in only: spends a few cents of E2B budget (one sandbox, one Firefox
install, two fixture pages). Run from backend/:

    FIELD_LIVE_VERIFY=1 python -m pytest tests/test_browser_live.py -s

Positive fixture renders prefixed status text ("Status: Preparing") with a
working advance button (the mission f30ec35cdc49 shape): advance and persist
steps must PASS. Negative fixture has a placebo advance button that changes
nothing: the advance step must FAIL, proving the fix did not weaken the
assertion into a substring match on control wording.
"""

import os

import pytest

from app.core.config import get_settings
from app.services.browser_workflow import (
    BROWSER_ASSERT_PATH,
    BROWSER_SCRIPT_PATH,
    run_browser_tests,
)
from app.services.e2b_verification import BROWSER_SETUP_COMMANDS

LIVE = os.environ.get("FIELD_LIVE_VERIFY") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="FIELD_LIVE_VERIFY=1 not set")

FIXTURE_PORT = 8901

_FIXTURE_SHELL = """<!doctype html>
<html><head><meta charset="utf-8"><title>Fixture</title></head>
<body>
<h1>Counter and Kitchen Orders</h1>
<form id="create">
<label for="order-details">Order details</label>
<input id="order-details" type="text" placeholder="e.g. 2 burgers, fries">
<button type="submit">Add Order</button>
</form>
<ul id="queue"></ul>
<script>
const KEY = "%s";
const NEXT = {"Received": "Preparing", "Preparing": "Ready", "Ready": "Completed"};
let orders = [];
try { orders = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { orders = []; }
function save() { localStorage.setItem(KEY, JSON.stringify(orders)); }
function render() {
  const ul = document.getElementById("queue");
  ul.innerHTML = "";
  for (const o of orders) {
    const li = document.createElement("li");
    const id = document.createElement("span"); id.textContent = "ID " + o.id;
    const contents = document.createElement("span"); contents.textContent = o.contents;
    const status = document.createElement("span"); status.textContent = "Status: " + o.status;
    li.appendChild(id); li.appendChild(document.createTextNode(" "));
    li.appendChild(contents); li.appendChild(document.createTextNode(" "));
    li.appendChild(status); li.appendChild(document.createTextNode(" "));
    const next = NEXT[o.status];
    if (next) {
      const btn = document.createElement("button");
      btn.type = "button"; btn.textContent = "Advance to " + next;
      btn.onclick = %s;
      li.appendChild(btn);
    }
    ul.appendChild(li);
  }
}
document.getElementById("create").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("order-details");
  const text = input.value.trim();
  if (!text) return;
  orders.push({id: "order-" + Date.now(), contents: text, status: "Received"});
  save(); render(); input.value = "";
});
render();
</script>
</body></html>
"""

POSITIVE_HTML = _FIXTURE_SHELL % (
    "fixture-positive-v1",
    "() => { o.status = next; save(); render(); }",
)
NEGATIVE_HTML = _FIXTURE_SHELL % (
    "fixture-negative-v1",
    "() => { /* placebo: intentionally changes nothing */ }",
)


def _serve_fixtures(sbx) -> None:
    sbx.files.write("/tmp/fixture/positive.html", POSITIVE_HTML)
    sbx.files.write("/tmp/fixture/negative.html", NEGATIVE_HTML)
    sbx.commands.run(
        f"python3 -m http.server {FIXTURE_PORT} --directory /tmp/fixture",
        background=True,
        # Background timeout is a kill deadline: keep the server alive for the run.
        timeout=1200,
    )
    ready = sbx.commands.run(
        f"for i in $(seq 1 30); do curl -sf http://localhost:{FIXTURE_PORT}/positive.html"
        " >/dev/null && break; sleep 2; done; "
        f"curl -sf http://localhost:{FIXTURE_PORT}/negative.html >/dev/null",
        timeout=120,
    )
    assert ready.exit_code == 0, "fixture server never became ready"


def _install_browser(sbx) -> None:
    for label, cmd, cmd_timeout in BROWSER_SETUP_COMMANDS:
        result = sbx.commands.run(cmd, timeout=cmd_timeout)
        assert result.exit_code == 0, f"{label} failed"


def _steps_by_name(result):
    assert result.available, result.error
    return {step.name: step for step in result.steps}


def test_browser_button_branch_fixtures() -> None:
    from e2b import Sandbox

    settings = get_settings()
    if not settings.e2b_configured:
        pytest.skip("E2B_API_KEY not configured")

    sbx = Sandbox.create(timeout=1200, api_key=settings.e2b_api_key)
    try:
        _serve_fixtures(sbx)
        _install_browser(sbx)

        base = f"http://localhost:{FIXTURE_PORT}"
        positive = _steps_by_name(run_browser_tests(sbx, f"{base}/positive.html"))
        assert positive["load"].status.value == "pass", positive["load"].detail
        assert positive["create"].status.value == "pass", positive["create"].detail
        assert positive["advance"].status.value == "pass", positive["advance"].detail
        assert positive["persist"].status.value == "pass", positive["persist"].detail

        # E2B files.write cannot overwrite: clear the script files before re-running.
        sbx.commands.run(f"rm -f {BROWSER_ASSERT_PATH} {BROWSER_SCRIPT_PATH}", timeout=30)
        negative = _steps_by_name(run_browser_tests(sbx, f"{base}/negative.html"))
        assert negative["create"].status.value == "pass", negative["create"].detail
        assert negative["advance"].status.value == "fail", "placebo advance must fail"
        assert "not shown after advance" in negative["advance"].detail
        assert negative["persist"].status.value == "pass", negative["persist"].detail
    finally:
        try:
            sbx.kill()
        except Exception:
            pass
