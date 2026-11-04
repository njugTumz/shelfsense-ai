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
    try:
        data = json.loads(_extract_output_text(payload))
    except ValueError as exc:
        raise SparkError(f"malformed JSON: {exc}") from exc
    usage = payload.get("usage", {}) or {}
    return data, {k: usage.get(k, 0) for k in ("input_tokens", "output_tokens") if k in usage}


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

    Retries once with a corrective instruction when the model returns
    malformed JSON (plan.md section 9). Raises SparkError otherwise.
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
    last_error = ""
    current_input: Any = input_text
    for attempt in (1, 2):
        body["input"] = current_input
        try:
            return _post_responses(body, timeout_s)
        except SparkError as exc:
            if not str(exc).startswith("malformed JSON"):
                raise
            last_error = f"malformed JSON (attempt {attempt}): {exc}"
            current_input = (
                f"{input_text}\n\nYour previous reply was not valid "
                f"{schema_name} JSON ({exc}). Reply again with ONLY valid "
                f"{schema_name} JSON."
            )
    raise SparkError(last_error)


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

    Retries once on malformed JSON by resending with a corrective note.
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
    except SparkError as exc:
        if not str(exc).startswith("malformed JSON"):
            raise
        content.append({
            "type": "input_text",
            "text": f"Your previous reply was not valid {schema_name} JSON ({exc}). "
            f"Reply again with ONLY valid {schema_name} JSON.",
        })
        body["input"] = [{"type": "message", "role": "user", "content": content}]
        return _post_responses(body, timeout_s)
