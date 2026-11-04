"""On-demand live preview of a mission's stored generated files.

The verification sandbox is always killed after the checks, so a business
owner cannot open the generated app afterwards. This service relaunches a
fresh E2B sandbox from the mission's persisted files using FIELD-fixed
commands only: no Spark, transcription, video analysis, generation, or
verification runs here.

Previews are temporary by E2B design (sandbox lifetime caps), never
permanent hosting: the persisted ``expires_at`` is the honest live-until
time. At most one live preview sandbox exists per mission: an active
preview is returned as-is, and an expired one is killed (best-effort)
before a replacement launches.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from app.core.config import get_settings
from app.core.store import load_mission, save_mission, utc_now_iso
from app.demo.restaurant_app import APP_ROOT
from app.models.mission import Mission, MissionPreview
from app.services.interview import LifecycleError

#: Preview lifetime. E2B caps sandbox life at 1h (Hobby) / 24h (Pro);
#: 55 minutes stays under both caps with margin for clock skew.
PREVIEW_TTL_S = 3300
PREVIEW_PORT = 5173
PREVIEW_POLL_S = 90
COMMAND_TIMEOUT_S = 600
ERROR_BUDGET = 500


class PreviewError(Exception):
    """E2B preview launch failed (provider-side, safe to retry)."""


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def preview_is_active(preview: MissionPreview | None, now_iso: str | None = None) -> bool:
    """A preview is active only with a URL whose expiry is still ahead."""
    if preview is None or not preview.preview_url or not preview.expires_at:
        return False
    expires = _parse_time(preview.expires_at)
    now = _parse_time(now_iso) if now_iso is not None else datetime.now(timezone.utc)
    if expires is None or now is None:
        return False
    return expires > now


def _kill_sandbox_quietly(sandbox_cls, api_key: str, sandbox_id: str) -> None:
    try:
        sandbox_cls.connect(sandbox_id, api_key=api_key).kill()
    except Exception:  # noqa: BLE001 - dead sandboxes cannot be killed; TTL caps cost
        pass


def _run_or_raise(sbx, label: str, cmd: str, **kwargs) -> None:
    try:
        sbx.commands.run(cmd, **kwargs)
    except Exception as exc:  # noqa: BLE001 - SDK raises (carrying output) on non-zero exit
        out = getattr(exc, "stdout", "") or ""
        err = getattr(exc, "stderr", "") or ""
        raise PreviewError(
            f"{label} failed: {exc}\nstdout={out[-800:]}\nstderr={err[-800:]}"
        ) from exc


def _poll_preview(url: str) -> None:
    deadline = time.time() + PREVIEW_POLL_S
    last = "no response yet"
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=10.0, follow_redirects=True)
            if resp.status_code == 200 and '<div id="root">' in resp.text:
                return
            last = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}"
        time.sleep(3)
    raise PreviewError(f"Preview did not serve the app ({last}).")


def launch_preview(mission_id: str, db_path=None) -> Mission:
    """Launch (or reuse) a temporary live preview for a mission's stored files.

    Returns the mission with ``preview`` populated. Reuses an active preview
    without touching E2B; kills an expired sandbox before replacing it.
    Generated files, verification, and mission status are never modified.
    """
    mission = load_mission(mission_id, db_path)
    if mission is None:
        raise KeyError(f"Unknown mission: {mission_id}")
    files = dict(mission.files) if mission.files else {}
    if not files:
        raise LifecycleError(f"Mission {mission_id} has no generated application files to preview.")
    if preview_is_active(mission.preview):
        return mission

    settings = get_settings()
    if not settings.e2b_configured:
        raise PreviewError("E2B unavailable: E2B_API_KEY is not configured.")
    from e2b import Sandbox

    old_id = mission.preview.sandbox_id if mission.preview else ""
    if old_id:
        _kill_sandbox_quietly(Sandbox, settings.e2b_api_key, old_id)

    started_at = utc_now_iso()
    sbx = Sandbox.create(timeout=PREVIEW_TTL_S, api_key=settings.e2b_api_key)
    try:
        for path, data in files.items():
            sbx.files.write(path, data)
        _run_or_raise(sbx, "npm install", "npm install --no-audit --no-fund",
                      cwd=APP_ROOT, timeout=COMMAND_TIMEOUT_S)
        _run_or_raise(sbx, "production build", "npm run build",
                      cwd=APP_ROOT, timeout=COMMAND_TIMEOUT_S)
        sbx.commands.run(
            f"npm run preview -- --port {PREVIEW_PORT} --host 0.0.0.0",
            cwd=APP_ROOT,
            background=True,
            timeout=PREVIEW_TTL_S,  # background timeout is a kill deadline: match sandbox TTL
        )
        url = f"https://{sbx.get_host(PREVIEW_PORT)}"
        _poll_preview(url)
    except Exception as exc:
        try:
            sbx.kill()
        except Exception:  # noqa: BLE001 - kill is best-effort; TTL caps cost
            pass
        detail = str(exc) if isinstance(exc, PreviewError) else f"E2B run failed: {exc}"
        mission.preview = MissionPreview(error=detail[:ERROR_BUDGET], started_at=started_at)
        mission.updated_at = utc_now_iso()
        save_mission(mission, db_path)
        if isinstance(exc, PreviewError):
            raise
        raise PreviewError(detail[:ERROR_BUDGET]) from exc

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=PREVIEW_TTL_S - 60)
    ).isoformat(timespec="seconds")
    mission.preview = MissionPreview(
        sandbox_id=sbx.sandbox_id,
        preview_url=url,
        started_at=started_at,
        expires_at=expires_at,
    )
    mission.updated_at = utc_now_iso()
    save_mission(mission, db_path)
    return mission
