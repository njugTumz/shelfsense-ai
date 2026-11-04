#!/usr/bin/env python3
"""Spike caller: poll one Modal Glimmer endpoint to ready, run ONE inference.

PASS = HTTP 200 + non-empty assistant content from genuine model output.
Full JSON saved to scripts/smoke/out/glimmer_inference.json (git-ignored).
Run: backend/.venv/bin/python scripts/smoke/glimmer_infer.py <base-url>
"""

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fail_with_error, report, save_text  # noqa: E402

NAME = "glimmer_infer"
READY_TIMEOUT_S = 1500
POLL_S = 15

PROMPT = """\
You are reviewing a small React component for an order dashboard. Find real bugs only.

```tsx
function OrderRow({ order, onStatus }) {
  return (
    <li>
      <span>{order.customer}</span>
      <select value={order.status} onChange={(e) => onStatus(order.id, e.target.value)}>
        <option>Received</option>
        <option>Preparing</option>
        <option>Ready</option>
      </select>
    </li>
  );
}
```

Reply with a short numbered list of genuine defects (at most 3). Do not invent files or APIs."""

BODY = {
    "model": "muse-glimmer",
    "messages": [{"role": "user", "content": PROMPT}],
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 64,
    "max_tokens": 256,
}


def main() -> int:
    started = time.perf_counter()
    if len(sys.argv) < 2:
        print("CONFIG ERROR: pass the Modal web endpoint base URL as argv[1].")
        return 2
    base = sys.argv[1].rstrip("/")
    host = base.split("https://", 1)[-1]
    print(f"endpoint_host={host[:12]}... (full URL in out/glimmer_inference.json)")

    ready_at = None
    deadline = time.time() + READY_TIMEOUT_S
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base}/health", timeout=10.0)
            if resp.status_code == 200:
                ready_at = time.perf_counter() - started
                print(f"server ready after {ready_at:.0f}s")
                break
        except httpx.HTTPError:
            pass
        time.sleep(POLL_S)
    if ready_at is None:
        return fail_with_error(NAME, started, "SERVER NEVER READY", f"no /health 200 within {READY_TIMEOUT_S}s")

    t0 = time.perf_counter()
    try:
        resp = httpx.post(f"{base}/v1/chat/completions", json=BODY, timeout=600.0)
    except httpx.HTTPError as exc:
        return fail_with_error(NAME, started, f"TRANSPORT ERROR: {type(exc).__name__}", str(exc))
    infer_s = time.perf_counter() - t0
    if resp.status_code != 200:
        return fail_with_error(NAME, started, f"HTTP {resp.status_code}", resp.text)

    try:
        payload = resp.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return fail_with_error(NAME, started, "BAD RESPONSE SHAPE", resp.text[:2000])
    if not content or not content.strip():
        return fail_with_error(NAME, started, "EMPTY MODEL OUTPUT", resp.text[:2000])

    save_text("glimmer_inference.json", json.dumps(
        {"base_url": base, "request": BODY, "response": payload}, indent=2))
    usage = payload.get("usage", {})
    details = (
        f"cold_start_s={ready_at:.0f} inference_s={infer_s:.1f} usage={json.dumps(usage)}\n"
        f"--- model output (verbatim, truncated) ---\n{content.strip()[:900]}"
    )
    return report(NAME, True, time.perf_counter() - started, details)


if __name__ == "__main__":
    raise SystemExit(main())
