#!/usr/bin/env python3
"""Smoke test: convert audio to mono 16-bit PCM WAV, call Voice Transcribe.

PASS = HTTP 200 + JSON response containing a transcript + verified WAV shape.
Run: backend/.venv/bin/python scripts/smoke/transcribe.py [audio.wav]
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    OUT_DIR,
    cost_gate,
    die_config,
    fail_with_error,
    get_env,
    report,
)

NAME = "transcribe"
NATIVE_RATE = 24000


def probe_wav(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(out.stdout)
    stream = info["streams"][0]
    return {
        "codec": stream["codec_name"],
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "duration_s": float(info["format"]["duration"]),
    }


def main() -> int:
    started = time.perf_counter()
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DIR / "audio.wav"
    if not src.exists():
        return die_config(f"audio not found: {src}. Run frames.py first.")
    wav = OUT_DIR / "audio_24k_mono.wav"

    conv = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1",
         "-ar", str(NATIVE_RATE), "-c:a", "pcm_s16le", "-map_metadata", "-1",
         str(wav)],
        capture_output=True, text=True,
    )
    if conv.returncode != 0 or not wav.exists():
        return fail_with_error(NAME, started, "FFMPEG CONVERT FAILED", conv.stderr)
    spec = probe_wav(wav)
    print(f"converted: {wav.name} {spec['codec']} {spec['sample_rate']}Hz "
          f"{spec['channels']}ch {spec['duration_s']:.1f}s size={wav.stat().st_size}B")
    if not (spec["codec"] == "pcm_s16le" and spec["channels"] == 1
            and spec["sample_rate"] in (16000, 24000)):
        return fail_with_error(NAME, started, "WAV SPEC MISMATCH", json.dumps(spec))
    if spec["duration_s"] > 600 or wav.stat().st_size > 32_000_000:
        return fail_with_error(NAME, started, "OVER API CAPS (10min/32MB)", json.dumps(spec))

    base_url = get_env("META_MODEL_BASE_URL").rstrip("/")
    api_key = get_env("META_MODEL_API_KEY")
    model = get_env("MUSE_TRANSCRIBE_MODEL_ID")
    print(f"model={model} endpoint={base_url}/asr/transcribe")
    cost_gate(spec["duration_s"] / 3600 * 0.18, f"{spec['duration_s']:.0f}s audio @ $0.18/h")

    request_part = json.dumps({"model": model, "audioEncoding": "WAV", "mode": "ENDPOINTING"})
    try:
        response = httpx.post(
            f"{base_url}/asr/transcribe",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            files={
                "request": (None, request_part, "application/json"),
                "audio": (wav.name, wav.read_bytes(), "audio/wav"),
            },
            timeout=300.0,
        )
    except httpx.HTTPError as exc:
        return fail_with_error(NAME, started, f"TRANSPORT ERROR: {type(exc).__name__}", str(exc))
    if response.status_code != 200:
        return fail_with_error(NAME, started, f"HTTP {response.status_code}", response.text)

    payload = response.json()
    if "transcript" not in payload:
        return fail_with_error(NAME, started, "NO transcript KEY", response.text[:2000])
    turns = payload.get("turns", [])
    billed_s = int(spec["duration_s"])
    details = (
        f"transcript={json.dumps(payload['transcript'])[:800]}\n"
        f"turns={len(turns)} audioDurationMs={payload.get('audioDurationMs')} "
        f"estimated_billed_cost=${billed_s / 3600 * 0.18:.5f}"
    )
    return report(NAME, True, time.perf_counter() - started, details)


if __name__ == "__main__":
    raise SystemExit(main())
