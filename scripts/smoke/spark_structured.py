#!/usr/bin/env python3
"""Smoke test: one structured-output Responses call to Muse Spark.

Uses text.format JSON schema, reasoning effort low, generous max_output_tokens.
PASS = HTTP 200 + output_text parses as JSON with the required keys.
Run: backend/.venv/bin/python scripts/smoke/spark_structured.py
"""

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    cost_gate,
    fail_with_error,
    get_env,
    report,
    spark_cost_usd,
)

NAME = "spark_structured"

SCHEMA = {
    "type": "object",
    "properties": {
        "dish": {"type": "string"},
        "quantity": {"type": "integer"},
        "notes": {"type": "string"},
    },
    "required": ["dish", "quantity", "notes"],
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
    base_url = get_env("META_MODEL_BASE_URL").rstrip("/")
    api_key = get_env("META_MODEL_API_KEY")
    model = get_env("MUSE_SPARK_MODEL_ID")
    print(f"model={model} endpoint={base_url}/responses")
    cost_gate(0.01, "upper bound at Standard rates for a tiny call")

    body = {
        "model": model,
        "input": 'Extract the order as JSON: "Two margherita pizzas, one without basil, for table 4."',
        "text": {
            "format": {
                "type": "json_schema",
                "name": "OrderUpdate",
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
            timeout=120.0,
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
    missing = [k for k in ("dish", "quantity", "notes") if k not in data]
    if missing:
        return fail_with_error(NAME, started, f"MISSING KEYS: {missing}", text)
    details = (
        f"parsed={json.dumps(data)} status={payload.get('status')}\n"
        f"usage: input_tokens={in_tok} output_tokens={out_tok} "
        f"estimated_billed_cost=${cost:.5f}"
    )
    return report(NAME, True, time.perf_counter() - started, details)


if __name__ == "__main__":
    raise SystemExit(main())
