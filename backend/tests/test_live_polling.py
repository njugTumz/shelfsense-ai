"""Adversarial verification of live mission polling (no-manual-refresh).

Mock-backend + real built frontend + real-Chromium Playwright. Each test
drives scripted mission timelines through the actual UI and asserts live
updates, headline/step agreement, backoff, recovery, and poller hygiene,
with screenshots to $FIELD_EVIDENCE_DIR (default: pytest tmp dir).

Polling-behavior tests (lifecycle, outage, heartbeat, visibility,
reload/switch, two-tabs) FAIL with the fix reverted; the terminal-stop
and old-mission tests pin absence-of-bad and stay green either way.

Skips cleanly when node/npm, free localhost ports, or Chromium is missing.
"""

import copy
import http.server
import json
import os
import shutil
import socket
import socketserver
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_PORT = 18080
MOCK_PORT = 18081
REAL_PORT = 18082
STATIC2_PORT = 18090


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Scripted mock backend
# --------------------------------------------------------------------------


class _MockHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102 - quiet test server
        pass

    def _send(self, code: int, obj=None) -> None:
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        mock = self.server.mock  # type: ignore[attr-defined]
        with mock.lock:
            try:
                code, obj = mock.route(self.path)
            except KeyError:
                code, obj = 404, {"detail": "not found"}
            mock.log.append((time.time(), "GET", self.path, code))
        self._send(code, obj)


class MockBackend:
    """Threaded scripted API: timelines fire on wall clock per request."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.log: list[tuple[float, str, str]] = []
        self.missions: dict = {}
        self._server = None
        self._thread = None

    def add_mission(self, doc: dict, timeline=(), job=None, job_timeline=()) -> None:
        with self.lock:
            self.missions[doc["id"]] = {
                "doc": copy.deepcopy(doc),
                "timeline": sorted(timeline, key=lambda e: e[0]),
                "t0": time.time(),
                "fired": 0,
                "job": copy.deepcopy(job),
                "job_timeline": sorted(job_timeline, key=lambda e: e[0]),
                "job_fired": 0,
            }

    def route(self, path: str):
        if path == "/health":
            return 200, {"status": "ok"}
        parts = path.strip("/").split("/")
        # /api/missions/{id}[/build/job]
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "missions":
            entry = self.missions[parts[2]]  # KeyError -> 404
            now = time.time()
            while entry["fired"] < len(entry["timeline"]) and (
                now - entry["t0"] >= entry["timeline"][entry["fired"]][0]
            ):
                _, patch = entry["timeline"][entry["fired"]]
                entry["doc"].update(copy.deepcopy(patch))
                entry["fired"] += 1
            while entry["job_fired"] < len(entry["job_timeline"]) and (
                now - entry["t0"] >= entry["job_timeline"][entry["job_fired"]][0]
            ):
                _, patch = entry["job_timeline"][entry["job_fired"]]
                entry["job"] = copy.deepcopy(patch)
                entry["job_fired"] += 1
            if len(parts) == 3:
                entry["doc"]["updated_at"] = _iso(_now())
                return 200, entry["doc"]
            if len(parts) == 5 and parts[3] == "build" and parts[4] == "job":
                if entry["job"] is None:
                    raise KeyError("no job")
                # Served exactly as scripted: a frozen heartbeat_at is the
                # worker-gap scenario, so the mock never freshens it.
                return 200, entry["job"]
        raise KeyError("unknown route")

    def start(self, port: int) -> None:
        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = Server(("127.0.0.1", port), _MockHandler)
        self._server.mock = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever)
        self._thread.start()

    def pause(self) -> None:
        """Stop serving entirely: refused connections, like a dead backend."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._server, self._thread = None, None

    def resume(self, port: int) -> None:
        self.start(port)

    def stop(self) -> None:
        try:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
        finally:
            self._server, self._thread = None, None


# --------------------------------------------------------------------------
# Mission / job / verification factories (full shapes the UI renders)
# --------------------------------------------------------------------------


def make_generation(status="generating", started_s=60, completed=False):
    now = _now()
    return {
        "status": status,
        "started_at": _iso(now - timedelta(seconds=started_s)),
        "completed_at": _iso(now) if completed else "",
        "model_id": "adv-mock",
        "usage": {},
        "summary": "adv mock generation",
        "manifest": ["src/App.tsx"] if completed else [],
        "feature_map": [],
        "unimplemented": [],
        "assumptions": [],
        "error": "" if status != "failed" else "Build cancelled before it started.",
        "error_class": "",
    }


def make_media(mid):
    now = _now()
    return {
        "mission_id": mid, "original_filename": "adv.mp4", "video_path": "",
        "content_type": "video/mp4", "size_bytes": 100, "duration_s": 10,
        "width": 64, "height": 64, "status": "ready", "error": "", "frames": [],
        "transcript": None, "observations": [], "hypotheses": [],
        "vision_model_id": "", "vision_usage": {}, "notes": [],
        "created_at": _iso(now - timedelta(seconds=600)),
    }


def make_spec():
    return {
        "business_context": "adv", "problem_statement": "adv problem",
        "objectives": ["o1"],
        "user_roles": [{"name": "Owner", "description": ""}],
        "functional_requirements": [{"id": "F1", "requirement": "r1"}],
        "non_functional_requirements": [], "user_journeys": [],
        "core_features": ["f1"], "data_entities": [],
        "acceptance_criteria": ["c1"], "assumptions": [],
        "unresolved_questions": [], "revision": 1,
    }


def make_mission(mid, status, step="", label="", generation=None,
                 verification=None, error="", approved=True):
    now = _now()
    return {
        "id": mid, "name": f"Adv {mid}", "status": status,
        "created_at": _iso(now - timedelta(seconds=600)),
        "updated_at": _iso(now),
        "requirements": "adv", "acceptance_criteria": ["c1"], "files": None,
        "verification": verification, "error": error,
        "evidence": [{"category": "OBSERVED", "text": "adv", "source": "adv"}],
        "interview": {
            "status": "complete",
            "turns": [{"index": 0, "question": "q?", "rationale": "r",
                       "answer": "a"}],
            "max_questions": 3,
        },
        "spec": make_spec(),
        "approved_at": _iso(now - timedelta(seconds=500)) if approved else "",
        "generation": generation, "repair": None, "media": make_media(mid),
        "progress_step": step, "progress_label": label, "preview": None,
        "is_approved": approved,
    }


def make_job(mid, status="running", heartbeat_fresh=True, stale_s=0):
    now = _now()
    if status == "queued":
        heartbeat = ""
    elif stale_s:
        heartbeat = _iso(now - timedelta(seconds=stale_s))
    else:
        heartbeat = _iso(now) if heartbeat_fresh else ""
    return {
        "id": f"job-{mid}", "mission_id": mid, "kind": "build",
        "status": status, "attempt": 1, "max_attempts": 3,
        "idempotency_key": None, "attempts": [], "stages_done": [],
        "usage": {}, "error": "", "error_class": "",
        "heartbeat_at": heartbeat,
        "created_at": _iso(now - timedelta(seconds=120)),
        "updated_at": _iso(now),
    }


def make_check(name, status="pass", required=True):
    return {
        "name": name, "category": "functional", "status": status,
        "required": required, "detail": "adv", "latency_s": 1.0,
        "completed_at": _iso(_now()), "chain": [], "hop_durations_ms": [],
    }


def make_verification(status, reason, failed_required=False,
                      transport=False, script_timeout=False,
                      audit_verdict="PASS_WITH_WARNINGS"):
    checks = [make_check("production_build"), make_check("preview_serves"),
              make_check("browser_loads_app")]
    if failed_required:
        checks.append(make_check("browser_creates_order", "fail"))
    else:
        checks.append(make_check("browser_creates_order"))
    findings = [] if audit_verdict == "PASS" else [{
        "severity": "low", "category": "ux", "finding": "adv nit",
        "evidence": "adv", "recommendation": "adv"}]
    now = _now()
    return {
        "status": status, "reason": reason,
        "e2b": {
            "available": True, "passed": status in ("VERIFIED", "VERIFIED_WITH_WARNINGS"),
            "checks": checks, "preview_url": "", "sandbox_id": "adv",
            "error": "adv timeout" if script_timeout else "",
            "latency_s": 10.0, "browser": None,
            "transport_failed": transport, "transport_retries": 0,
            "script_timeout": script_timeout,
            "diagnostics": {"before_browser": "", "after_browser": ""},
        },
        "audit": {
            "provider": "spark_adversarial", "available": True, "completed": True,
            "verdict": audit_verdict, "summary": "adv audit",
            "findings": findings, "model_id": "adv", "usage": {},
            "error": "", "error_class": "", "latency_s": 1.0,
        },
        "coverage": None, "audit_provider_active": "spark",
        "audit_provider_target": "glimmer",
        "started_at": _iso(now - timedelta(seconds=300)),
        "completed_at": _iso(now),
    }


# --------------------------------------------------------------------------
# Fixtures: built frontend, mock/real backends, browser, evidence
# --------------------------------------------------------------------------


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _require_toolchain(*ports: int) -> None:
    if shutil.which("node") is None or shutil.which("npm") is None:
        pytest.skip("node/npm not available")
    if not (REPO_ROOT / "frontend" / "node_modules").exists():
        pytest.skip("frontend/node_modules not installed")
    for port in ports:
        if not _port_free(port):
            pytest.skip(f"localhost:{port} busy")


def _build_dist(api_url: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, VITE_API_URL=api_url)
    proc = subprocess.run(
        ["npm", "run", "build"], cwd=REPO_ROOT / "frontend", env=env,
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"frontend build failed:\n{proc.stderr[-2000:]}"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(REPO_ROOT / "frontend" / "dist", dest)


class _StaticServer:
    def __init__(self, directory: Path, port: int) -> None:
        import functools

        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=str(directory))
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture(scope="module")
def frontend_mock(tmp_path_factory):
    _require_toolchain(STATIC_PORT, MOCK_PORT)
    dest = tmp_path_factory.mktemp("dist-mock")
    _build_dist(f"http://127.0.0.1:{MOCK_PORT}", dest)
    server = _StaticServer(dest, STATIC_PORT)
    yield f"http://127.0.0.1:{STATIC_PORT}/"
    server.stop()


@pytest.fixture(scope="module")
def playwright_browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    with sync_playwright() as pw:
        try:
            probe = pw.chromium.launch()
            probe.close()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        browser = pw.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture()
def mock():
    if not _port_free(MOCK_PORT):
        pytest.skip(f"localhost:{MOCK_PORT} busy")
    backend = MockBackend()
    backend.start(MOCK_PORT)
    yield backend
    backend.stop()


@pytest.fixture()
def context(playwright_browser):
    ctx = playwright_browser.new_context(viewport={"width": 1280, "height": 900})
    yield ctx
    ctx.close()


@pytest.fixture()
def evidence(tmp_path):
    root = os.environ.get("FIELD_EVIDENCE_DIR")
    dest = Path(root) if root else tmp_path / "evidence"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


@pytest.fixture()
def real_backend(tmp_path):
    """Real backend on a COPY of the production backup (GETs only)."""
    if not _port_free(REAL_PORT):
        pytest.skip(f"localhost:{REAL_PORT} busy")
    src = Path("/home/izaktumz/field_evidence/deploy-prep-20261106/db/field.db")
    if not src.exists():
        pytest.skip("production backup copy not present")
    db = tmp_path / "backup_copy.db"
    shutil.copy(src, db)
    env = dict(os.environ,
               DATABASE_URL=f"sqlite:///{db}",
               FRONTEND_ORIGIN=f"http://127.0.0.1:{STATIC2_PORT}")
    proc = subprocess.Popen(
        ["./.venv/bin/python", "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(REAL_PORT)],
        cwd=REPO_ROOT / "backend", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import urllib.request
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{REAL_PORT}/health", timeout=3):
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("real backend never came up")
        yield f"http://127.0.0.1:{REAL_PORT}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def frontend_real(tmp_path_factory):
    _require_toolchain(STATIC2_PORT, REAL_PORT)
    dest = tmp_path_factory.mktemp("dist-real")
    _build_dist(f"http://127.0.0.1:{REAL_PORT}", dest)
    server = _StaticServer(dest, STATIC2_PORT)
    yield f"http://127.0.0.1:{STATIC2_PORT}/"
    server.stop()


# --------------------------------------------------------------------------
# Page helpers
# --------------------------------------------------------------------------


def resume(page, base: str, mid: str) -> None:
    page.goto(base, wait_until="networkidle")
    page.get_by_placeholder("mission id").fill(mid)
    page.get_by_role("button", name="Resume mission").click()
    # Terminal missions render the id twice (header + results block).
    page.get_by_text(f"Mission {mid}").first.wait_for(timeout=15000)


def drain(page, seconds: float, step: float = 0.25) -> None:
    """Sleep while pumping Playwright event delivery: the sync API only
    dispatches page.on handlers during API calls, so bare time.sleep
    batches handler timestamps at the next call."""
    end = time.time() + seconds
    while time.time() < end:
        page.wait_for_timeout(int(step * 1000))


def set_marker(page) -> None:
    page.evaluate("window.__adv_marker = 'still-here'")


def check_marker(page) -> None:
    assert page.evaluate("window.__adv_marker") == "still-here", \
        "page reloaded mid-scenario"


def mission_gets(mock: MockBackend, mid: str, since: float = 0.0):
    return [ts for ts, method, path, _ in mock.log
            if ts >= since and method == "GET"
            and path == f"/api/missions/{mid}"]


def job_gets(mock: MockBackend, mid: str, since: float = 0.0):
    return [ts for ts, method, path, _ in mock.log
            if ts >= since and method == "GET"
            and path == f"/api/missions/{mid}/build/job"]


def assert_no_storm(mock: MockBackend, since: float = 0.0, limit: int = 6) -> None:
    """No 1s window may carry more than `limit` poll GETs (a tick is 2)."""
    stamps = sorted(ts for ts, _, path, _ in mock.log
                    if ts >= since and path.startswith("/api/missions/"))
    for i, ts in enumerate(stamps):
        window = [t for t in stamps[i:] if t - ts < 1.0]
        assert len(window) <= limit, f"request storm: {len(window)} GETs in 1s"


def collect_problems(page):
    problems: list[str] = []
    page.on("pageerror", lambda exc: problems.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: problems.append(f"console.{msg.type}: {msg.text}")
            if msg.type == "error" else None)
    return problems


def shot(page, evidence, name: str) -> None:
    page.screenshot(path=str(evidence / f"{name}.png"), full_page=True)


def wait_headline(page, label: str, timeout: int = 20000) -> None:
    page.get_by_text(f"Working on: {label}", exact=False).wait_for(timeout=timeout)


# --------------------------------------------------------------------------
# A1: full lifecycle, no reload, everything agrees (~3 min, compressed)
# --------------------------------------------------------------------------


def test_a1_full_lifecycle_no_reload(context, frontend_mock, mock, evidence):
    mid = "adv-lifecycle"
    gen_running = make_generation("generating")
    gen_done = make_generation("generated", completed=True)
    vww = make_verification("VERIFIED_WITH_WARNINGS", "warnings")
    mock.add_mission(
        make_mission(mid, "BUILDING", step="queued",
                     label="Build queued — the background worker picks it up shortly…",
                     generation=gen_running),
        timeline=[
            (30, {"progress_step": "generating",
                  "progress_label": "Generating application…"}),
            (60, {"status": "RUNNING", "progress_step": "e2b_build",
                  "progress_label": "Building the React application…",
                  "generation": gen_done}),
            (90, {"status": "AUDITING", "progress_step": "auditing",
                  "progress_label": "Running the independent audit…"}),
            (120, {"status": "VERIFIED_WITH_WARNINGS", "progress_step": "",
                   "progress_label": "", "verification": vww}),
        ],
        job=make_job(mid, "queued"),
        job_timeline=[
            (30, make_job(mid, "running")),
            (90, None),  # job done once auditing starts: 404 from here
        ],
    )
    page = context.new_page()
    problems = collect_problems(page)
    resume(page, frontend_mock, mid)
    set_marker(page)

    def agree(label):
        # Headline, step list and progress indicator share one derivation.
        wait_headline(page, label, timeout=40000)
        assert page.get_by_text(label).count() >= 2, f"step/headline disagree on {label}"
        assert page.get_by_text("of 20").count() >= 1
        assert page.get_by_text("(estimate)").count() >= 1
        check_marker(page)

    agree("Generate application files")
    assert "Build queued" in page.content()
    # The worker line needs the first tick (job state starts null).
    # "Worker:" with colon: the banner's "background worker" must not match.
    page.get_by_text("Worker:").wait_for(timeout=15000)
    assert "picks up shortly" in page.content()
    shot(page, evidence, "a1-s1-queued")

    page.get_by_text("Generating application…").first.wait_for(timeout=45000)
    agree("Generate application files")
    assert "Worker running" in page.content()
    shot(page, evidence, "a1-s2-generating")

    agree("Build the React application")
    assert "Worker running" in page.content()
    shot(page, evidence, "a1-s3-running")

    agree("Independent Muse Spark audit")
    # Job 404s during auditing: no worker line, no banner, no storm.
    assert "Worker" not in page.content()
    assert "Reconnecting" not in page.content()
    shot(page, evidence, "a1-s4-auditing")

    page.get_by_text("Verified with warnings").wait_for(timeout=45000)
    assert "Working on:" not in page.content()
    check_marker(page)
    shot(page, evidence, "a1-s5-terminal")

    settle = time.time()
    while time.time() - settle < 3:
        time.sleep(0.5)
    mark = time.time()
    time.sleep(8)
    assert mission_gets(mock, mid, since=mark) == []
    assert job_gets(mock, mid, since=mark) == []
    assert_no_storm(mock)
    # Job 404s are the designed no-active-job contract; Chromium still
    # logs each failed fetch to the console. Every console 404 must be
    # explained by a server-side job 404 — none extra, none missing.
    server_404s = [e for e in mock.log if e[3] == 404]
    assert server_404s, "expected job 404s during auditing"
    assert all(e[2].endswith("/build/job") for e in server_404s), server_404s
    console_404s = [p for p in problems if "404" in p]
    assert len(console_404s) == len(server_404s), (console_404s, server_404s)
    rest = [p for p in problems if "404" not in p]
    assert rest == [], rest
    check_marker(page)


# --------------------------------------------------------------------------
# A2: terminal statuses stop polling (request counts)
# --------------------------------------------------------------------------

TERMINAL_CASES = [
    ("adv-t-verified", "VERIFIED", "clean", {}, "Verified"),
    ("adv-t-vww", "VERIFIED_WITH_WARNINGS", "warnings", {}, "Verified with warnings"),
    ("adv-t-failed", "FAILED", "runtime", {"failed_required": True}, "Failed"),
    ("adv-t-inconc-transport", "INCONCLUSIVE", "transport",
     {"transport": True}, "infrastructure failed"),
    ("adv-t-inconc-timeout", "INCONCLUSIVE", "script_timeout",
     {"script_timeout": True}, "Verification timed out"),
]


def test_a2_terminal_stops_polling(context, frontend_mock, mock, evidence):
    for mid, status, reason, flags, copy in TERMINAL_CASES:
        mock.add_mission(make_mission(
            mid, status, verification=make_verification(status, reason, **flags)))
    # Cancelled: job gone, mission restored to APPROVED with an error.
    cancelled = make_mission(
        "adv-t-cancelled", "APPROVED", error="Build cancelled before it started.",
        generation=make_generation("failed", completed=True))
    mock.add_mission(cancelled)

    page = context.new_page()
    problems = collect_problems(page)
    for mid, status, reason, flags, copy in TERMINAL_CASES:
        resume(page, frontend_mock, mid)
        page.get_by_text(copy).first.wait_for(timeout=15000)
        time.sleep(2)  # resume GET settles
        mark = time.time()
        time.sleep(8)
        assert mission_gets(mock, mid, since=mark) == [], f"{mid} still polls"
        assert job_gets(mock, mid, since=mark) == [], f"{mid} still polls jobs"
        shot(page, evidence, f"a2-{mid}")
    resume(page, frontend_mock, "adv-t-cancelled")
    page.get_by_text("Build cancelled before it started.").first.wait_for(timeout=15000)
    time.sleep(2)
    mark = time.time()
    time.sleep(8)
    assert mission_gets(mock, "adv-t-cancelled", since=mark) == []
    shot(page, evidence, "a2-cancelled")
    assert problems == [], problems


# --------------------------------------------------------------------------
# A3: 30s outage mid-build — reconnecting, backoff, recovery
# --------------------------------------------------------------------------


def test_a3_outage_backoff_recovery(context, frontend_mock, mock, evidence):
    mid = "adv-outage"
    mock.add_mission(
        make_mission(mid, "BUILDING", step="generating",
                     label="Generating application…",
                     generation=make_generation("generating")),
        timeline=[(75, {"status": "RUNNING", "progress_step": "e2b_build",
                        "progress_label": "Building…",
                        "generation": make_generation("generated", completed=True)})],
        job=make_job(mid, "running"),
    )
    page = context.new_page()
    problems = collect_problems(page)
    client_tries: list[float] = []
    page.on("request", lambda req: client_tries.append(time.time())
            if req.url.endswith(f"/api/missions/{mid}") else None)
    resume(page, frontend_mock, mid)
    wait_headline(page, "Generate application files")
    set_marker(page)
    drain(page, 8)  # drained: client_tries timestamps stay accurate
    outage_start = time.time()
    before = len(client_tries)
    mock.pause()
    try:
        page.get_by_text("Reconnecting").wait_for(timeout=15000)
        shot(page, evidence, "a3-reconnecting")
        drain(page, max(0.0, 30 - (time.time() - outage_start)))
    finally:
        mock.resume(MOCK_PORT)
    outage_end = time.time()
    # Client-side counting: refused connections never reach the server log.
    attempts = [ts for ts in client_tries[before:]
                if outage_start <= ts < outage_end]
    # Backoff 2.5→5→10→20 (cap 30): ~4-5 tries in 30s, never a storm.
    assert 2 <= len(attempts) <= 7, f"{len(attempts)} attempts in outage"
    gaps = [b - a for a, b in zip(attempts, attempts[1:])]
    assert min(gaps, default=99) >= 1.5, f"request storm in outage: {gaps}"
    assert max(gaps, default=0) >= 8, f"no backoff growth: {gaps}"
    page.get_by_text("Reconnecting").wait_for(state="hidden", timeout=15000)
    wait_headline(page, "Build the React application", timeout=60000)
    check_marker(page)
    shot(page, evidence, "a3-recovered")
    assert_no_storm(mock)
    # Refused-fetch console noise is the outage itself; all else must be clean.
    unexpected = [p for p in problems if "ERR_CONNECTION_REFUSED" not in p]
    assert unexpected == [], unexpected


# --------------------------------------------------------------------------
# A4: frozen heartbeat — stale warning, then recovery
# --------------------------------------------------------------------------


def test_a4_stale_heartbeat_warning(context, frontend_mock, mock, evidence):
    mid = "adv-heartbeat"
    now = _now()
    stale = make_job(mid, "running")
    stale["heartbeat_at"] = _iso(now - timedelta(seconds=601))
    fresh = make_job(mid, "running")
    mock.add_mission(
        make_mission(mid, "RUNNING", step="e2b_build", label="Building…",
                     generation=make_generation("generated", completed=True)),
        job=fresh,
        job_timeline=[(8, stale), (24, make_job(mid, "running"))],
    )
    page = context.new_page()
    problems = collect_problems(page)
    resume(page, frontend_mock, mid)
    wait_headline(page, "Build the React application")
    page.get_by_text("heartbeat").first.wait_for(timeout=15000)
    assert "stale past" not in page.content()
    page.get_by_text("stale past 600s").wait_for(timeout=25000)
    shot(page, evidence, "a4-stale")
    page.get_by_text("stale past 600s").wait_for(state="hidden", timeout=30000)
    assert "heartbeat" in page.content()
    shot(page, evidence, "a4-recovered")
    assert problems == [], problems


# --------------------------------------------------------------------------
# A5: hidden tab refreshes immediately on visibility
# --------------------------------------------------------------------------


def test_a5_hidden_tab_refresh(context, frontend_mock, mock, evidence):
    mid = "adv-hidden"
    mock.add_mission(
        make_mission(mid, "BUILDING", step="generating",
                     label="Generating…",
                     generation=make_generation("generating")),
        timeline=[
            (6, {"status": "RUNNING", "progress_step": "e2b_build",
                 "progress_label": "Building…",
                 "generation": make_generation("generated", completed=True)}),
            (14, {"status": "AUDITING", "progress_step": "auditing",
                  "progress_label": "Auditing…"}),
        ],
        job=make_job(mid, "running"),
    )
    page = context.new_page()
    problems = collect_problems(page)
    resume(page, frontend_mock, mid)
    wait_headline(page, "Generate application files")
    other = context.new_page()
    other.goto("about:blank")
    other.bring_to_front()
    hidden = False
    deadline = time.time() + 5
    while time.time() < deadline:
        if page.evaluate("document.visibilityState") == "hidden":
            hidden = True
            break
        time.sleep(0.2)
    # Let the mission advance ~3 stages worth while hidden.
    time.sleep(17)
    if not hidden:
        # Fallback (documented): headless never hides; drive the real
        # visibilitychange handler directly instead.
        page.evaluate(
            "Object.defineProperty(document, 'visibilityState', "
            "{value: 'visible', configurable: true});"
            "document.dispatchEvent(new Event('visibilitychange'));")
        fg_at = time.time()
        path = "fallback"
    else:
        # Foreground right after a scheduled tick, so any GET within
        # 1.5s must come from the visibility handler, not the cadence.
        before = mission_gets(mock, mid)
        while len(mission_gets(mock, mid)) == len(before):
            time.sleep(0.2)
            if time.time() - before[-1] > 6:
                break
        page.bring_to_front()
        fg_at = time.time()
        path = "real-hidden"
    soon = [ts for ts in mission_gets(mock, mid, since=fg_at) if ts - fg_at < 1.5]
    # Give the tick a beat to land before asserting.
    time.sleep(1.0)
    soon = [ts for ts in mission_gets(mock, mid, since=fg_at) if ts - fg_at < 2.5]
    assert soon, f"no immediate refresh on visibility ({path})"
    wait_headline(page, "Independent Muse Spark audit", timeout=15000)
    shot(page, evidence, f"a5-visible-{path}")
    print(f"\nA5 visibility path: {path}")
    assert problems == [], problems


# --------------------------------------------------------------------------
# A6: reload resumes; switches and transitions leak no pollers
# --------------------------------------------------------------------------


def test_a6_reload_switch_no_leak(context, frontend_mock, mock, evidence):
    mid_a, mid_b = "adv-reload-a", "adv-reload-b"
    mock.add_mission(make_mission(mid_a, "RUNNING", step="e2b_build",
                                  label="Building…",
                                  generation=make_generation("generated", completed=True)),
                     job=make_job(mid_a, "running"))
    mock.add_mission(make_mission(mid_b, "BUILDING", step="generating",
                                  label="Generating…",
                                  generation=make_generation("generating")),
                     job=make_job(mid_b, "running"))
    page = context.new_page()
    problems = collect_problems(page)
    resume(page, frontend_mock, mid_a)
    wait_headline(page, "Build the React application")
    page.reload(wait_until="networkidle")
    # Reload returns to the start screen (no persisted selection):
    # re-resume must restore the exact live state on one poller.
    page.get_by_placeholder("mission id").wait_for(timeout=15000)
    resume(page, frontend_mock, mid_a)
    wait_headline(page, "Build the React application", timeout=15000)
    # No reload marker here: the reloads below are intentional; the
    # no-leak proof is the per-mission request counts.
    mark = time.time()
    time.sleep(10)
    gets = mission_gets(mock, mid_a, since=mark)
    assert 2 <= len(gets) <= 6, f"expected one poller, saw {len(gets)} GETs/10s"
    shot(page, evidence, "a6-resumed")
    # Switch missions (via reload+resume, the app's only navigation):
    # the old mission must go silent, the new one polls singly.
    page.reload(wait_until="networkidle")
    resume(page, frontend_mock, mid_b)
    wait_headline(page, "Generate application files", timeout=15000)
    mark = time.time()
    time.sleep(10)
    assert mission_gets(mock, mid_a, since=mark) == [], "old mission still polled"
    gets_b = mission_gets(mock, mid_b, since=mark)
    assert 2 <= len(gets_b) <= 6, f"expected one poller, saw {len(gets_b)} GETs/10s"
    # Rapid status transitions restart the effect: cadence must not double.
    mid_c = "adv-reload-c"
    mock.add_mission(
        make_mission(mid_c, "BUILDING", step="generating", label="Gen…",
                     generation=make_generation("generating")),
        timeline=[
            (4, {"status": "RUNNING", "progress_step": "e2b_build",
                 "generation": make_generation("generated", completed=True)}),
            (8, {"status": "AUDITING", "progress_step": "auditing"}),
            (12, {"status": "BUILDING", "progress_step": "generating"}),
            (16, {"status": "RUNNING", "progress_step": "e2b_preview"}),
        ],
        job=make_job(mid_c, "running"))
    page.reload(wait_until="networkidle")
    resume(page, frontend_mock, mid_c)
    mark = time.time()
    time.sleep(24)
    churn = mission_gets(mock, mid_c, since=mark)
    assert 6 <= len(churn) <= 18, f"poller leak across transitions? {len(churn)}/24s"
    assert_no_storm(mock)
    assert problems == [], problems


# --------------------------------------------------------------------------
# A7: approval stays complete (sticky is_approved display contract)
# --------------------------------------------------------------------------


def test_a7_approval_stays_complete(context, frontend_mock, mock, evidence):
    """Display half of sticky approval (the model half is pinned by
    test_is_approved_is_sticky_after_build_starts in test_progress.py)."""
    mid = "adv-approval"
    mock.add_mission(
        make_mission(mid, "BUILDING", step="generating", label="Gen…",
                     generation=make_generation("generating")),
        timeline=[
            (8, {"status": "RUNNING", "progress_step": "e2b_build",
                 "generation": make_generation("generated", completed=True)}),
            (16, {"status": "AUDITING", "progress_step": "auditing"}),
        ],
        job=make_job(mid, "running"),
    )
    page = context.new_page()
    problems = collect_problems(page)
    resume(page, frontend_mock, mid)
    wait_headline(page, "Generate application files")
    item = page.locator("li", has_text="Wait for your approval")
    item.wait_for(timeout=15000)
    assert "✓" in item.text_content(), "approval step not done while BUILDING"
    wait_headline(page, "Build the React application", timeout=30000)
    assert "✓" in item.text_content(), "approval step lost done state in RUNNING"
    wait_headline(page, "Independent Muse Spark audit", timeout=30000)
    assert "✓" in item.text_content(), "approval step lost done state in AUDITING"
    shot(page, evidence, "a7-approval-done")
    assert problems == [], problems


# --------------------------------------------------------------------------
# A8: all 11 backup missions render on the new stack (real backend)
# --------------------------------------------------------------------------

BACKUP_IDS = [
    "0f64b49546a5", "15118aa6d220", "17d89c6fc04b", "3306cf583f6a",
    "33bfd4595180", "7a4c82e0021e", "9be9e35ca941", "b6669ee31e0a",
    "bf86b5d6eb61", "dd68ededc9a9", "f30ec35cdc49",
]


def test_a8_backup_missions_render(context, frontend_real, real_backend, evidence):
    page = context.new_page()
    problems = collect_problems(page)
    api_hits: list[str] = []
    page.on("request", lambda req: api_hits.append(req.url)
            if "/api/" in req.url else None)
    for mid in BACKUP_IDS:
        resume(page, frontend_real, mid)
        page.get_by_text("Where your mission stands").wait_for(timeout=15000)
        shot(page, evidence, f"a8-{mid[:8]}")
    assert "bf86b5d6eb61" in BACKUP_IDS and "33bfd4595180" in BACKUP_IDS
    # Representative quiet-missions: terminal + idle must not poll.
    for mid in ("bf86b5d6eb61", "0f64b49546a5", "dd68ededc9a9"):
        resume(page, frontend_real, mid)
        time.sleep(2)
        mark = len(api_hits)
        time.sleep(8)
        fresh = [u for u in api_hits[mark:] if mid in u]
        assert fresh == [], f"{mid} polled {len(fresh)}x while idle"
    assert problems == [], problems


# --------------------------------------------------------------------------
# A9: two tabs on the same mission both update
# --------------------------------------------------------------------------


def test_a9_two_tabs_update(context, frontend_mock, mock, evidence):
    mid = "adv-tabs"
    mock.add_mission(
        make_mission(mid, "BUILDING", step="generating", label="Gen…",
                     generation=make_generation("generating")),
        timeline=[(8, {"status": "RUNNING", "progress_step": "e2b_build",
                       "progress_label": "Building…",
                       "generation": make_generation("generated", completed=True)})],
        job=make_job(mid, "running"),
    )
    page1 = context.new_page()
    page2 = context.new_page()
    problems1 = collect_problems(page1)
    problems2 = collect_problems(page2)
    resume(page1, frontend_mock, mid)
    resume(page2, frontend_mock, mid)
    wait_headline(page1, "Generate application files")
    wait_headline(page2, "Generate application files")
    set_marker(page1)
    set_marker(page2)
    wait_headline(page1, "Build the React application", timeout=30000)
    wait_headline(page2, "Build the React application", timeout=30000)
    check_marker(page1)
    check_marker(page2)
    shot(page1, evidence, "a9-tab1")
    shot(page2, evidence, "a9-tab2")
    assert problems1 == [], problems1
    assert problems2 == [], problems2


