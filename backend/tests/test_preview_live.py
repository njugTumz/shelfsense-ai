"""Live preview test: real E2B relaunch from stored files, no models involved.

Opt-in only: spends a few cents of E2B budget (one sandbox, ~2 minutes).
Run from backend/:

    FIELD_LIVE_VERIFY=1 python -m pytest tests/test_preview_live.py -s

Uses the reference app files purely as a deterministic build fixture; the
test proves relaunch plumbing (upload -> install -> build -> serve -> 200),
not model output. The sandbox is killed afterwards.
"""

import os

import httpx
import pytest

from app.core.config import get_settings
from app.core.store import save_mission
from app.demo.restaurant_app import demo_files
from app.models.mission import Mission, MissionStatus
from app.services.preview import launch_preview, preview_is_active

LIVE = os.environ.get("FIELD_LIVE_VERIFY") == "1"

pytestmark = pytest.mark.skipif(not LIVE, reason="FIELD_LIVE_VERIFY=1 not set")


def test_live_preview_relaunch_serves_200(tmp_path) -> None:
    settings = get_settings()
    if not settings.e2b_configured:
        pytest.skip("E2B_API_KEY not configured")

    db = tmp_path / "live_preview.db"
    save_mission(
        Mission(id="live-preview", status=MissionStatus.VERIFIED, files=demo_files()),
        db,
    )
    result = launch_preview("live-preview", db_path=db)
    assert result.preview is not None
    assert result.preview.error == ""
    assert result.preview.preview_url.startswith("https://")
    assert result.preview.sandbox_id != ""
    assert preview_is_active(result.preview) is True

    try:
        resp = httpx.get(result.preview.preview_url, timeout=15.0, follow_redirects=True)
        assert resp.status_code == 200
        assert '<div id="root">' in resp.text
    finally:
        from e2b import Sandbox

        try:
            Sandbox.connect(result.preview.sandbox_id, api_key=settings.e2b_api_key).kill()
        except Exception:
            pass
