#!/usr/bin/env python3
"""Smoke test: extract 6 frames + audio from sample.mp4 (local ffmpeg only).

PASS = 6 non-empty JPGs + 1 verified WAV exist under scripts/smoke/out/.
Run: python3 scripts/smoke/frames.py [video.mp4]
"""

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OUT_DIR, SMOKE_DIR, die_config, fail_with_error, report  # noqa: E402

NAME = "frames"
N_FRAMES = 6


def run(cmd: list) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print(f"CONFIG ERROR: required tool not found: {cmd[0]}")
        raise SystemExit(2)


def main() -> int:
    started = time.perf_counter()
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SMOKE_DIR / "sample.mp4"
    if not src.exists():
        return die_config(f"video not found: {src}")
    frames_dir = OUT_DIR / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_path = OUT_DIR / "audio.wav"

    probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
                 "-of", "json", str(src)])
    if probe.returncode != 0:
        return fail_with_error(NAME, started, "FFPROBE FAILED", probe.stderr)
    fmt = json.loads(probe.stdout)["format"]
    duration = float(fmt["duration"])
    print(f"source: {src.name} duration={duration:.1f}s size={int(fmt['size'])}B")

    stamps = [(i + 0.5) * duration / N_FRAMES for i in range(N_FRAMES)]
    for i, stamp in enumerate(stamps, start=1):
        out = frames_dir / f"frame_{i:02d}.jpg"
        conv = run(["ffmpeg", "-y", "-v", "error", "-ss", f"{stamp:.2f}",
                    "-i", str(src), "-frames:v", "1", "-q:v", "3", str(out)])
        if conv.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            return fail_with_error(NAME, started, f"FRAME {i} FAILED @{stamp:.1f}s", conv.stderr)

    audio = run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn",
                 "-c:a", "pcm_s16le", str(audio_path)])
    if audio.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        return fail_with_error(NAME, started, "AUDIO EXTRACT FAILED", audio.stderr)
    aprobe = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                  "-show_entries", "stream=codec_name,sample_rate,channels",
                  "-of", "json", str(audio_path)])
    if aprobe.returncode != 0:
        return fail_with_error(NAME, started, "AUDIO VERIFY FAILED", aprobe.stderr)
    astream = json.loads(aprobe.stdout)["streams"][0]

    sizes = sorted(p.stat().st_size for p in frames_dir.glob("frame_*.jpg"))
    details = (
        f"frames={len(sizes)} sizes={sizes} audio={audio_path.stat().st_size}B "
        f"({astream['codec_name']} {astream['sample_rate']}Hz {astream['channels']}ch) cost=$0.00"
    )
    return report(NAME, True, time.perf_counter() - started, details)


if __name__ == "__main__":
    raise SystemExit(main())
