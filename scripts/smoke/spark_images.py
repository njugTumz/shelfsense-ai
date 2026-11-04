#!/usr/bin/env python3
"""Smoke test: 3 extracted frames as input_image blocks in one user message.

Reads frames written by frames.py, sends them to Muse Spark, expects a
structured description. PASS = HTTP 200 + valid structured JSON.
Run: backend/.venv/bin/python scripts/smoke/spark_images.py [f1 f2 f3]
"""

import base64
import json
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
    spark_cost_usd,
)

NAME = "spark_images"

SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "visible_objects": {"type": "array", "items": {"type": "string"}},
        "people_count": {"type": "integer"},
    },
    "required": ["scene", "visible_objects", "people_count"],
    "additionalProperties": False,
}


def extract_output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text", "")
    return ""


def main() -> int:
    started = time.perf_counter()
    if len(sys.argv) > 1:
        frame_paths = [Path(a) for a in sys.argv[1:4]]
    else:
        frame_paths = [OUT_DIR / "frames" / f"frame_{n:02d}.jpg" for n in (1, 3, 5)]
    missing = [str(p) for p in frame_paths if not p.exists()]
    if missing:
        return die_config(f"frame(s) not found: {', '.join(missing)}. Run frames.py first.")

    base_url = get_env("META_MODEL_BASE_URL").rstrip("/")
    api_key = get_env("META_MODEL_API_KEY")
    model = get_env("MUSE_SPARK_MODEL_ID")
    print(f"model={model} frames={[p.name for p in frame_paths]}")
    cost_gate(0.02, "upper bound at Standard rates for 3 images + short output")

    content: list = [
        {
            "type": "input_text",
            "text": "Describe what is visible across these video frames as JSON.",
        }
    ]
    for path in frame_paths:
        b64 = base64.b64encode(path.read_bytes()).decode()
        content.append(
            {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
        )
    body = {
        "model": model,
        "input": [{"type": "message", "role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "FrameDescription",
                "schema": SCHEMA,
                "strict": False,
            }
        },
        "reasoning": {"effort": "low"},
        "max_output_tokens": 8192,
        "store": False,
    }
    try:
        response = httpx.post(
            f"{base_url}/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=180.0,
        )
    except httpx.HTTPError as exc:
        return fail_with_error(NAME, started, f"TRANSPORT ERROR: {type(exc).__name__}", str(exc))
    if response.status_code != 200:
        return fail_with_error(NAME, started, f"HTTP {response.status_code}", response.text)

    payload = response.json()
    text = extract_output_text(payload)
    usage = payload.get("usage", {})
    in_tok = int(usage.get("input_tokens", 0))
    out_tok = int(usage.get("output_tokens", 0))
    cost = spark_cost_usd(model, in_tok, out_tok)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return fail_with_error(NAME, started, "OUTPUT NOT JSON", text)
    missing_keys = [k for k in ("scene", "visible_objects", "people_count") if k not in data]
    if missing_keys:
        return fail_with_error(NAME, started, f"MISSING KEYS: {missing_keys}", text)
    details = (
        f"parsed={json.dumps(data)[:600]} status={payload.get('status')}\n"
        f"usage: input_tokens={in_tok} output_tokens={out_tok} "
        f"estimated_billed_cost=${cost:.5f}"
    )
    return report(NAME, True, time.perf_counter() - started, details)


if __name__ == "__main__":
    raise SystemExit(main())
