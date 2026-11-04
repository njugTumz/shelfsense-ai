"""Shared Muse Spark client (Responses API, structured output).

Single home of the verified request shape from docs/integration-notes.md:
``POST {base}/responses`` with ``text.format`` JSON schema. Used by the
adversarial auditor, the interviewer, and requirements generation so the
integration stays identical everywhere. Model ID always comes from
settings — never hardcoded, never guessed.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings

#: Server-side provider-call log (container stdout). Entries carry site,
#: effort, budgets, timings, outcome class, and token usage only — never
#: prompt contents, model output, or credentials.
logger = logging.getLogger("field.spark")


class SparkError(Exception):
    """A real Spark call failed (transport, HTTP, or malformed output)."""


class SparkTransientError(SparkError):
    """Retryable provider/network failure (timeouts, 408/5xx, dropped peers).

    Carries an optional server-directed wait; the retry loop honors it.
    """

    def __init__(self, message: str, retry_after_s: float | None = None):
        self.retry_after_s = retry_after_s
        super().__init__(message)


class SparkRateLimitError(SparkTransientError):
    """HTTP 429: back off (Retry-After when the provider sends one)."""


class SparkAuthError(SparkError):
    """HTTP 401/403: credentials rejected. Never retried."""


class SparkBadRequestError(SparkError):
    """Other 4xx: the request itself is wrong. Never retried."""


class SparkUnavailableError(SparkError):
    """Credentials/model ID are not configured; no call was attempted."""


class SparkTruncatedError(SparkError):
    """The provider cut the response short (e.g. output token budget).

    Carries safe diagnostics only: the provider's stop reason, the
    received character count, and token usage. Never prompt contents.
    """

    def __init__(self, reason: str, output_chars: int, usage: dict[str, int]):
        self.reason = reason
        self.output_chars = output_chars
        self.usage = usage
        super().__init__(
            f"truncated response (reason={reason}, output_chars={output_chars}, "
            f"output_tokens={usage.get('output_tokens', '?')})"
        )


#: Upper bound for the one-shot budget escalation on truncation. Well
#: under the documented 131k model maximum.
MAX_OUTPUT_ESCALATION_CAP = 65536

USAGE_KEYS = ("input_tokens", "output_tokens", "reasoning_tokens")

#: Split transport timeouts. Connect/pool failures fail fast (a dead peer
#: is never worth waiting for); only the read side scales with reasoning
#: effort and output budget, via each call site's ``timeout_s``.
CONNECT_TIMEOUT_S = 10.0
WRITE_TIMEOUT_S = 60.0
POOL_TIMEOUT_S = 10.0

#: Bounded transport retry: at most this many HTTP attempts per logical
#: _post_responses call (initial + retries). Separate from — and inside —
#: the content-level recovery (truncation/malformed-JSON) above it.
MAX_TRANSPORT_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 2.0
RETRY_MAX_DELAY_S = 60.0

#: Swappable only so tests can capture sleeps without waiting.
_sleep = time.sleep


def _extract_output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text", "")
    return ""


def _parse_retry_after(headers: Any) -> float | None:
    """Retry-After header as seconds (delta or HTTP-date). None if absent."""
    try:
        raw = (headers.get("retry-after") or headers.get("Retry-After") or "").strip()
    except (AttributeError, TypeError):
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


def retry_delay_s(attempt: int, retry_after_s: float | None = None) -> float:
    """Backoff after completed attempt N (1-based): exp with jitter.

    Jitter is uniform in [delay/2, delay] so concurrent missions do not
    retry in lockstep; a server Retry-After always wins as a floor.
    """
    import random

    capped = min(RETRY_BASE_DELAY_S * (2 ** (attempt - 1)), RETRY_MAX_DELAY_S)
    delay = random.uniform(capped / 2.0, capped)
    if retry_after_s:
        delay = max(delay, retry_after_s)
    return delay


def _classify_http_error(status: int, response: Any) -> SparkError:
    """Map a non-200 status to the narrowest error (never 200 here)."""
    text = (response.text or "")[:1000]
    if status == 429:
        return SparkRateLimitError(
            f"HTTP 429 rate limited: {text}",
            retry_after_s=_parse_retry_after(getattr(response, "headers", {})),
        )
    if status == 408 or status >= 500:
        return SparkTransientError(f"HTTP {status}: {text}")
    if status in (401, 403):
        return SparkAuthError(f"HTTP {status}: {text}")
    return SparkBadRequestError(f"HTTP {status}: {text}")


def http_post_with_retry(
    url: str,
    site: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    timeout: httpx.Timeout | float,
) -> httpx.Response:
    """POST with bounded retries on transient failures. Shared by the
    Responses client and the transcription call so both classify and back
    off identically. Returns the response (non-200 raises classified
    SparkError, except terminal transport exhaustion which also raises).
    """
    last: SparkTransientError | None = None
    for attempt in range(1, MAX_TRANSPORT_ATTEMPTS + 1):
        try:
            response = httpx.post(url, headers=headers, json=json, files=files, timeout=timeout)
        except httpx.HTTPError as exc:
            last = SparkTransientError(
                f"TRANSPORT ERROR: {type(exc).__name__}: {exc}")
        else:
            if response.status_code == 200:
                return response
            classified = _classify_http_error(response.status_code, response)
            if not isinstance(classified, SparkTransientError):
                raise classified
            last = classified
        if attempt >= MAX_TRANSPORT_ATTEMPTS:
            assert last is not None
            raise last
        delay = retry_delay_s(attempt, last.retry_after_s)
        logger.warning(
            "spark_retry site=%s attempt=%d/%d class=%s delay_s=%.1f",
            site, attempt, MAX_TRANSPORT_ATTEMPTS,
            type(last).__name__, delay,
        )
        _sleep(delay)
    assert False, "unreachable"


def _post_responses_once(
    body: dict[str, Any], timeout_s: float
) -> tuple[dict[str, Any], dict[str, int]]:
    """POST one Responses body, exactly one HTTP attempt."""
    settings = get_settings()
    transport = httpx.Timeout(
        connect=CONNECT_TIMEOUT_S, read=timeout_s,
        write=WRITE_TIMEOUT_S, pool=POOL_TIMEOUT_S,
    )
    try:
        response = httpx.post(
            f"{settings.meta_model_base_url}/responses",
            headers={"Authorization": f"Bearer {settings.meta_model_api_key}"},
            json=body,
            timeout=transport,
        )
    except httpx.HTTPError as exc:
        raise SparkTransientError(f"TRANSPORT ERROR: {type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise _classify_http_error(response.status_code, response)
    return _parse_response_payload(response)


def _parse_response_payload(
    response: Any,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Extract (parsed_json, usage) from a 200 Responses payload."""
    payload = response.json()
    text = _extract_output_text(payload)
    raw_usage = payload.get("usage", {}) or {}
    usage = {k: raw_usage[k] for k in USAGE_KEYS if k in raw_usage}
    if payload.get("status") == "incomplete":
        details = payload.get("incomplete_details") or {}
        raise SparkTruncatedError(details.get("reason", "unknown"), len(text), usage)
    try:
        data = json.loads(text)
    except ValueError as exc:
        if _smells_truncated(exc):
            raise SparkTruncatedError("suspected_truncation", len(text), usage) from exc
        raise SparkError(f"malformed JSON: {exc} (output_chars={len(text)})") from exc
    return data, usage


def _post_responses(
    body: dict[str, Any], timeout_s: float, site: str = "unknown"
) -> tuple[dict[str, Any], dict[str, int]]:
    """POST one Responses body with bounded transport retries.

    Only SparkTransientError (timeouts, dropped peers, 408/429/5xx) is
    retried, at most MAX_TRANSPORT_ATTEMPTS HTTP attempts total. Auth,
    bad-request, truncation, and malformed-output errors propagate
    immediately to their existing handlers.
    """
    settings = get_settings()
    transport = httpx.Timeout(
        connect=CONNECT_TIMEOUT_S, read=timeout_s,
        write=WRITE_TIMEOUT_S, pool=POOL_TIMEOUT_S,
    )
    response = http_post_with_retry(
        f"{settings.meta_model_base_url}/responses",
        site,
        headers={"Authorization": f"Bearer {settings.meta_model_api_key}"},
        json=body,
        timeout=transport,
    )
    return _parse_response_payload(response)


#: json.decode errors that mean "cut off mid-stream" rather than "wrong
#: shape". Schema-constrained output is structurally valid, so these
#: almost always indicate budget exhaustion even without an explicit
#: incomplete flag from the provider.
_TRUNCATION_SIGNATURES = (
    "Unterminated string",
    "Expecting ',' delimiter",
    "Expecting property name enclosed in double quotes",
)


def _smells_truncated(exc: ValueError) -> bool:
    message = str(exc)
    return any(sig in message for sig in _TRUNCATION_SIGNATURES)


def _exhausted_error(second: SparkTruncatedError, cap: int) -> SparkError:
    """Clear terminal error when the escalated retry also truncates."""
    return SparkError(
        f"response truncated by the output token budget on both attempts "
        f"(reason={second.reason}, output_chars={second.output_chars}, "
        f"escalated_cap={cap}); safe to retry"
    )


def _require_spark() -> None:
    if not get_settings().meta_configured:
        raise SparkUnavailableError(
            "Muse Spark unavailable: META_MODEL_API_KEY or MUSE_SPARK_MODEL_ID "
            "is not configured."
        )


class _timed_call:
    """Log one logical provider call: budgets, wall time, outcome, usage.

    Safe fields only (see ``logger``). Internal recovery retries are part
    of the same logical call; the outcome is terminal (ok or the final
    error class).
    """

    def __init__(self, site: str, effort: str, max_output_tokens: int, timeout_s: float):
        self.site = site
        self.effort = effort
        self.max_output_tokens = max_output_tokens
        self.timeout_s = timeout_s
        self.usage: dict[str, int] = {}
        self._start = 0.0

    def __enter__(self) -> _timed_call:
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        ms = int((time.monotonic() - self._start) * 1000)
        outcome = "ok" if exc_type is None else type(exc).__name__
        logger.info(
            "spark_call site=%s effort=%s max_tokens=%d timeout_s=%.0f ms=%d "
            "outcome=%s input_tokens=%s output_tokens=%s",
            self.site, self.effort, self.max_output_tokens, self.timeout_s, ms,
            outcome, self.usage.get("input_tokens", "?"),
            self.usage.get("output_tokens", "?"),
        )


def responses_structured(
    *,
    schema_name: str,
    schema: dict[str, Any],
    input_text: str,
    effort: str = "low",
    max_output_tokens: int = 8192,
    timeout_s: float = 180.0,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Run one structured-output call. Returns (parsed_json, usage).

    Bounded recovery (at most two provider calls): a truncated response
    is retried once with a doubled output budget and the original input
    (a corrective note cannot fix a cap problem); complete-but-invalid
    JSON is retried once with a corrective instruction (plan.md §9).
    Raises SparkError otherwise.
    """
    _require_spark()
    settings = get_settings()
    body: dict[str, Any] = {
        "model": settings.muse_spark_model_id,
        "input": input_text,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": False,
            }
        },
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    with _timed_call(schema_name, effort, max_output_tokens, timeout_s) as timed:
        try:
            data, usage = _post_responses(body, timeout_s, site=schema_name)
        except SparkTruncatedError:
            escalated = min(max_output_tokens * 2, MAX_OUTPUT_ESCALATION_CAP)
            retry_body = {**body, "input": input_text, "max_output_tokens": escalated}
            try:
                data, usage = _post_responses(retry_body, timeout_s, site=schema_name)
            except SparkTruncatedError as second:
                raise _exhausted_error(second, escalated) from second
        except SparkError as exc:
            if not str(exc).startswith("malformed JSON"):
                raise
            retry_body = {
                **body,
                "input": (
                    f"{input_text}\n\nYour previous reply was not valid "
                    f"{schema_name} JSON ({exc}). Reply again with ONLY valid "
                    f"{schema_name} JSON."
                ),
            }
            try:
                data, usage = _post_responses(retry_body, timeout_s, site=schema_name)
            except SparkError as second:
                raise SparkError(
                    f"model returned invalid JSON on both attempts ({second}); "
                    f"safe to retry"
                ) from second
        timed.usage = usage
        return data, usage


def responses_structured_multimodal(
    *,
    schema_name: str,
    schema: dict[str, Any],
    instruction: str,
    images: list[tuple[str, str, bytes]],
    effort: str = "low",
    max_output_tokens: int = 8192,
    timeout_s: float = 180.0,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Structured call with image inputs. ``images`` is a list of
    (label, mime_type, raw_bytes); each image is preceded by its label
    so the model can reference frames (verified shape: ``input_image``
    blocks with base64 ``data:`` URLs inside one user message).

    Retries once on malformed JSON by resending with a corrective note,
    or once with a doubled output budget on truncation (original input).
    Raises SparkError otherwise.
    """
    _require_spark()
    settings = get_settings()
    content: list[dict[str, Any]] = [{"type": "input_text", "text": instruction}]
    for label, mime, raw in images:
        content.append({"type": "input_text", "text": label})
        content.append({
            "type": "input_image",
            "image_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
        })
    body: dict[str, Any] = {
        "model": settings.muse_spark_model_id,
        "input": [{"type": "message", "role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": False,
            }
        },
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    with _timed_call(schema_name, effort, max_output_tokens, timeout_s) as timed:
        try:
            data, usage = _post_responses(body, timeout_s, site=schema_name)
        except SparkTruncatedError:
            escalated = min(max_output_tokens * 2, MAX_OUTPUT_ESCALATION_CAP)
            body["max_output_tokens"] = escalated
            try:
                data, usage = _post_responses(body, timeout_s, site=schema_name)
            except SparkTruncatedError as second:
                raise _exhausted_error(second, escalated) from second
        except SparkError as exc:
            if not str(exc).startswith("malformed JSON"):
                raise
            content.append({
                "type": "input_text",
                "text": f"Your previous reply was not valid {schema_name} JSON ({exc}). "
                f"Reply again with ONLY valid {schema_name} JSON.",
            })
            body["input"] = [{"type": "message", "role": "user", "content": content}]
            try:
                data, usage = _post_responses(body, timeout_s, site=schema_name)
            except SparkError as second:
                raise SparkError(
                    f"model returned invalid JSON on both attempts ({second}); "
                    f"safe to retry"
                ) from second
        timed.usage = usage
        return data, usage
