"""Keyless tests: on-demand preview relaunch from stored generated files.

Run from backend/: python -m pytest tests/test_preview.py
Real E2B launch is proven by tests/test_preview_live.py. These tests use a
fake sandbox: no network, no model calls (Spark entry points are replaced
with hard failures).
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.store import load_mission, save_mission
from app.main import app
from app.models.mission import Mission, MissionPreview, MissionStatus


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


FILES = {
    "/home/user/app/package.json": '{"name": "app"}',
    "/home/user/app/src/App.tsx": "export default function App() { return null; }",
}


def _mission_with_files(db_path, preview=None):
    mission = Mission(id="p1", status=MissionStatus.VERIFIED_WITH_WARNINGS, files=dict(FILES))
    if preview is not None:
        mission.preview = preview
    return save_mission(mission, db_path)


class _FakeFiles:
    def __init__(self):
        self.written = {}

    def write(self, path, data):
        self.written[path] = data


class _FakeCommands:
    def __init__(self, fail_on=""):
        self.ran = []
        self.fail_on = fail_on

    def run(self, cmd, **kwargs):
        self.ran.append(cmd)
        if self.fail_on and self.fail_on in cmd:
            raise RuntimeError(f"exit=1: {cmd}")
        result = type("Result", (), {})()
        result.stdout = "ok"
        result.exit_code = 0
        return result


class _FakeSandbox:
    connects = []
    created = []

    def __init__(self, sandbox_id, fail_on=""):
        self.sandbox_id = sandbox_id
        self.files = _FakeFiles()
        self.commands = _FakeCommands(fail_on)
        self.killed = False
        type(self).created.append(self)

    def get_host(self, port):
        return f"{port}-{self.sandbox_id}.e2b.app"

    def kill(self):
        self.killed = True

    @classmethod
    def reset(cls):
        cls.connects = []
        cls.created = []

    @classmethod
    def fake_connect(cls, sandbox_id, api_key=None):
        cls.connects.append(sandbox_id)
        return cls(sandbox_id)


def _no_models(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("preview must not call models")

    monkeypatch.setattr("app.services.spark_client.responses_structured", boom)
    monkeypatch.setattr("app.services.spark_client.responses_structured_multimodal", boom)


def _fake_http_ok(monkeypatch):
    def fake_get(url, timeout=None, **kwargs):
        response = type("Response", (), {})()
        response.status_code = 200
        response.text = '<div id="root"></div>'
        return response

    monkeypatch.setattr("httpx.get", fake_get)


def test_preview_unknown_mission_404(db_path) -> None:
    from app.services.preview import launch_preview

    client = TestClient(app)
    assert client.post("/api/missions/nope/preview").status_code == 404
    with pytest.raises(KeyError):
        launch_preview("nope", db_path=db_path)


def test_preview_without_files_409(db_path) -> None:
    from app.services.interview import LifecycleError
    from app.services.preview import launch_preview

    client = TestClient(app)
    fresh = client.post("/api/missions", json={}).json()["id"]
    assert client.post(f"/api/missions/{fresh}/preview").status_code == 409

    save_mission(Mission(id="empty", files={}), db_path)
    with pytest.raises(LifecycleError):
        launch_preview("empty", db_path=db_path)


def test_preview_expiry_logic() -> None:
    from app.services.preview import preview_is_active

    assert preview_is_active(None) is False
    assert preview_is_active(MissionPreview()) is False
    active = MissionPreview(preview_url="https://x", expires_at="2999-01-01T00:00:00+00:00")
    assert preview_is_active(active, "2026-01-01T00:00:00+00:00") is True
    expired = MissionPreview(preview_url="https://x", expires_at="2020-01-01T00:00:00+00:00")
    assert preview_is_active(expired, "2026-01-01T00:00:00+00:00") is False
    broken = MissionPreview(preview_url="https://x", expires_at="not-a-time")
    assert preview_is_active(broken, "2026-01-01T00:00:00+00:00") is False


def test_preview_active_returns_existing_without_e2b(db_path, monkeypatch) -> None:
    from app.services.preview import launch_preview

    preview = MissionPreview(
        sandbox_id="live-1",
        preview_url="https://5173-live-1.e2b.app",
        started_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:00:00+00:00",
    )
    _mission_with_files(db_path, preview)

    def boom(*args, **kwargs):
        raise AssertionError("active preview must not touch E2B")

    monkeypatch.setattr("e2b.Sandbox.create", boom)
    monkeypatch.setattr("e2b.Sandbox.connect", boom)
    result = launch_preview("p1", db_path=db_path)
    assert result.preview is not None
    assert result.preview.sandbox_id == "live-1"
    assert result.preview.preview_url == "https://5173-live-1.e2b.app"


def test_preview_relaunch_uses_stored_files_without_models(db_path, monkeypatch) -> None:
    from app.services.preview import launch_preview

    _FakeSandbox.reset()
    _no_models(monkeypatch)
    _fake_http_ok(monkeypatch)
    old = MissionPreview(
        sandbox_id="old-1", preview_url="https://old", expires_at="2020-01-01T00:00:00+00:00"
    )
    _mission_with_files(db_path, old)

    monkeypatch.setattr("e2b.Sandbox.connect", _FakeSandbox.fake_connect)
    monkeypatch.setattr(
        "e2b.Sandbox.create", lambda timeout, api_key: _FakeSandbox("new-1")
    )
    result = launch_preview("p1", db_path=db_path)

    assert _FakeSandbox.connects == ["old-1"]
    assert len(_FakeSandbox.created) == 2  # connected old + created new
    new_box = [b for b in _FakeSandbox.created if b.sandbox_id == "new-1"][0]
    assert new_box.files.written == FILES
    assert any("npm install" in c for c in new_box.commands.ran)
    assert any("npm run build" in c for c in new_box.commands.ran)
    assert any("npm run preview" in c for c in new_box.commands.ran)
    assert result.preview is not None
    assert result.preview.preview_url == "https://5173-new-1.e2b.app"
    assert result.preview.error == ""
    persisted = load_mission("p1", db_path)
    assert persisted is not None and persisted.preview is not None
    assert persisted.preview.sandbox_id == "new-1"
    assert persisted.files == FILES
    assert persisted.status == MissionStatus.VERIFIED_WITH_WARNINGS


def test_preview_failure_records_error_and_cleans_up(db_path, monkeypatch) -> None:
    from app.services.preview import PreviewError, launch_preview

    _FakeSandbox.reset()
    _no_models(monkeypatch)
    _mission_with_files(db_path)

    monkeypatch.setattr(
        "e2b.Sandbox.create", lambda timeout, api_key: _FakeSandbox("doomed", fail_on="npm install")
    )
    with pytest.raises(PreviewError):
        launch_preview("p1", db_path=db_path)
    assert _FakeSandbox.created[0].killed is True
    persisted = load_mission("p1", db_path)
    assert persisted is not None and persisted.preview is not None
    assert persisted.preview.error != ""
    assert persisted.preview.preview_url == ""
    assert persisted.files == FILES


def test_preview_e2b_unconfigured_503(db_path, monkeypatch) -> None:
    from app.services.preview import PreviewError, launch_preview

    _mission_with_files(db_path)
    monkeypatch.setenv("E2B_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(PreviewError):
            launch_preview("p1", db_path=db_path)
    finally:
        get_settings.cache_clear()

    client = TestClient(app)
    monkeypatch.setenv("E2B_API_KEY", "")
    get_settings.cache_clear()
    try:
        fresh = client.post("/api/missions", json={"name": "x"}).json()["id"]
        from app.core.store import load_mission as load_default

        mission = load_default(fresh)
        assert mission is not None
        mission.files = dict(FILES)
        save_mission(mission)
        assert client.post(f"/api/missions/{fresh}/preview").status_code == 503
    finally:
        get_settings.cache_clear()
