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
from typing import Any

import httpx

from app.core.config import get_settings


class SparkError(Exception):
    """A real Spark call failed (transport, HTTP, or malformed output)."""


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


def _extract_output_text(payload: dict) -> str:
    for item in payload.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    return part.get("text", "")
    return ""


def _post_responses(
    body: dict[str, Any], timeout_s: float
) -> tuple[dict[str, Any], dict[str, int]]:
    """POST one Responses body. Returns (parsed_json, usage)."""
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.meta_model_base_url}/responses",
            headers={"Authorization": f"Bearer {settings.meta_model_api_key}"},
            json=body,
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        raise SparkError(f"TRANSPORT ERROR: {type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise SparkError(f"HTTP {response.status_code}: {response.text[:1000]}")
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
    try:
        return _post_responses(body, timeout_s)
    except SparkTruncatedError:
        escalated = min(max_output_tokens * 2, MAX_OUTPUT_ESCALATION_CAP)
        retry_body = {**body, "input": input_text, "max_output_tokens": escalated}
        try:
            return _post_responses(retry_body, timeout_s)
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
            return _post_responses(retry_body, timeout_s)
        except SparkError as second:
            raise SparkError(
                f"model returned invalid JSON on both attempts ({second}); "
                f"safe to retry"
            ) from second


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
    try:
        return _post_responses(body, timeout_s)
    except SparkTruncatedError:
        escalated = min(max_output_tokens * 2, MAX_OUTPUT_ESCALATION_CAP)
        body["max_output_tokens"] = escalated
        try:
            return _post_responses(body, timeout_s)
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
            return _post_responses(body, timeout_s)
        except SparkError as second:
            raise SparkError(
                f"model returned invalid JSON on both attempts ({second}); "
                f"safe to retry"
            ) from second
