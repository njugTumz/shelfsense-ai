"""Shared helpers for integration smoke scripts. Stdlib only.

Security: secret values are NEVER printed. Use redacted() before recording
any HTTP body, and save_text() writes to scripts/smoke/out/ (git-ignored).
"""

import os
import re
import sys
import time
from pathlib import Path

SMOKE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SMOKE_DIR.parent.parent
ENV_PATH = REPO_ROOT / ".env"
OUT_DIR = SMOKE_DIR / "out"

SECRET_NAME_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")


def load_dotenv() -> dict:
    """Parse the repo-root .env into a dict (no dependency, no overrides)."""
    values: dict = {}
    if not ENV_PATH.exists():
        return values
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values.setdefault(name, value)
    return values


_ENV_FILE = load_dotenv()


def get_env(name: str, required: bool = True) -> str:
    """Read a variable from the process env, falling back to .env."""
    value = os.environ.get(name, _ENV_FILE.get(name, ""))
    if required and not value:
        print(f"CONFIG ERROR: {name} is empty. Set it in {ENV_PATH}.")
        raise SystemExit(2)
    return value


def collect_secrets() -> list:
    """All non-empty secret-looking values, for redaction only."""
    secrets = []
    for source in (os.environ, _ENV_FILE):
        for name, value in source.items():
            if (
                value
                and len(value) >= 4
                and any(h in name.upper() for h in SECRET_NAME_HINTS)
            ):
                secrets.append(value)
    return secrets


def redacted(text: str) -> str:
    """Remove secret values and bearer tokens from text before recording."""
    out = _BEARER_RE.sub("Bearer ***REDACTED***", text)
    for secret in collect_secrets():
        if secret in out:
            out = out.replace(secret, "***REDACTED***")
    return out


def save_text(filename: str, text: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / filename
    path.write_text(text, encoding="utf-8")
    return path


def cost_gate(estimate_usd: float, basis: str) -> None:
    """Print the cost estimate. Refuse to proceed above the $0.50 approval line."""
    print(f"ESTIMATED COST: <=${estimate_usd:.4f} ({basis}; approval limit $0.50)")
    if estimate_usd > 0.50:
        print("COST GATE: estimate exceeds $0.50 — stopping for human approval.")
        raise SystemExit(3)


def report(name: str, ok: bool, latency_s: float, details: str = "") -> int:
    print(f"RESULT: {'PASS' if ok else 'FAIL'} {name} latency_s={latency_s:.1f}")
    if details:
        print(details)
    return 0 if ok else 1


def fail_with_error(name: str, started: float, status: str, body: str) -> int:
    """Record the real error (secrets removed) and report FAIL."""
    safe = redacted(f"{status}\n{body}")
    path = save_text(f"{name}_error.txt", safe)
    snippet = safe[:1200]
    return report(name, False, time.perf_counter() - started,
                  f"{status}\nFull redacted body saved to {path}\n---\n{snippet}")


def spark_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate billed cost at documented per-1M rates (labeled estimate)."""
    if "contributor" in model:
        return (input_tokens * 0.10 + output_tokens * 0.20) / 1_000_000
    return (input_tokens * 1.25 + output_tokens * 4.25) / 1_000_000


def die_config(message: str) -> int:
    print(f"CONFIG ERROR: {message}")
    return 2


if __name__ == "__main__":
    print("import-only helper", file=sys.stderr)
    raise SystemExit(1)
