"""Keyless tests: Spark truncation detection and bounded recovery.

Run from backend/: python -m pytest tests/test_spark_client.py
Provider truncation is simulated with fixed payloads; no network use.
"""

import json

import pytest

import app.services.spark_client as sc
from app.services.spark_client import (
    SparkError,
    SparkTruncatedError,
    get_settings,
    responses_structured,
)

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "string"}},
    "required": ["ok"],
    "additionalProperties": False,
}

FAKE_KEY = "test-key-not-a-secret"


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("META_MODEL_API_KEY", FAKE_KEY)
    monkeypatch.setenv("META_MODEL_BASE_URL", "https://models.test/v1")
    monkeypatch.setenv("MUSE_SPARK_MODEL_ID", "muse-spark-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _payload(text, status="completed", reason=None, usage=None):
    payload = {
        "status": status,
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": text}],
        }],
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }
    if reason is not None:
        payload["incomplete_details"] = {"reason": reason}
    return payload


def _stub(monkeypatch, payloads):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return _Resp(payloads[min(len(calls) - 1, len(payloads) - 1)])

    monkeypatch.setattr(sc.httpx, "post", fake_post)
    return calls


def test_valid_structured_response(configured, monkeypatch) -> None:
    calls = _stub(monkeypatch, [_payload('{"ok": "yes"}')])
    data, usage = responses_structured(
        schema_name="Probe", schema=SCHEMA, input_text="hi", max_output_tokens=100
    )
    assert data == {"ok": "yes"}
    assert usage["output_tokens"] == 5
    assert len(calls) == 1
    assert calls[0]["max_output_tokens"] == 100


def test_truncated_response_recovers_with_escalated_budget(configured, monkeypatch) -> None:
    cut = '{"ok": "unfinished val'
    calls = _stub(monkeypatch, [
        _payload(cut, status="incomplete", reason="max_output_tokens",
                 usage={"input_tokens": 50, "output_tokens": 100}),
        _payload('{"ok": "yes"}'),
    ])
    data, _ = responses_structured(
        schema_name="Probe", schema=SCHEMA, input_text="hi", max_output_tokens=100
    )
    assert data == {"ok": "yes"}
    assert len(calls) == 2
    assert calls[1]["max_output_tokens"] == 200
    assert calls[1]["input"] == "hi"


def test_unterminated_string_without_flag_escalates(configured, monkeypatch) -> None:
    calls = _stub(monkeypatch, [
        _payload('{"ok": "cut off mid-str'),
        _payload('{"ok": "yes"}'),
    ])
    data, _ = responses_structured(
        schema_name="Probe", schema=SCHEMA, input_text="hi", max_output_tokens=8000
    )
    assert data == {"ok": "yes"}
    assert len(calls) == 2
    assert calls[1]["max_output_tokens"] == 16000
    assert calls[1]["input"] == "hi"


def test_garbage_output_gets_corrective_retry(configured, monkeypatch) -> None:
    calls = _stub(monkeypatch, [
        _payload("not json at all"),
        _payload('{"ok": "yes"}'),
    ])
    data, _ = responses_structured(
        schema_name="Probe", schema=SCHEMA, input_text="hi", max_output_tokens=8000
    )
    assert data == {"ok": "yes"}
    assert len(calls) == 2
    assert calls[1]["max_output_tokens"] == 8000
    assert "ONLY valid Probe JSON" in calls[1]["input"]


def test_repeated_truncation_fails_clearly(configured, monkeypatch) -> None:
    cut = '{"ok": "unfinished val'
    calls = _stub(monkeypatch, [
        _payload(cut, status="incomplete", reason="max_output_tokens"),
        _payload(cut, status="incomplete", reason="max_output_tokens"),
    ])
    with pytest.raises(SparkError) as excinfo:
        responses_structured(
            schema_name="Probe", schema=SCHEMA, input_text="hi", max_output_tokens=100
        )
    message = str(excinfo.value)
    assert "truncated" in message.lower()
    assert "safe to retry" in message.lower()
    assert FAKE_KEY not in message
    assert len(calls) == 2
    assert [c["max_output_tokens"] for c in calls] == [100, 200]


def test_repeated_malformed_fails_clearly(configured, monkeypatch) -> None:
    calls = _stub(monkeypatch, [_payload("not json at all")] * 2)
    with pytest.raises(SparkError) as excinfo:
        responses_structured(schema_name="Probe", schema=SCHEMA, input_text="hi")
    assert "valid JSON" in str(excinfo.value)
    assert FAKE_KEY not in str(excinfo.value)
    assert len(calls) == 2


def test_truncation_error_carries_safe_diagnostics(configured, monkeypatch) -> None:
    _stub(monkeypatch, [
        _payload('{"ok": "cut', status="incomplete", reason="max_output_tokens",
                 usage={"input_tokens": 7, "output_tokens": 100, "reasoning_tokens": 60}),
    ])
    with pytest.raises(SparkTruncatedError) as excinfo:
        sc._post_responses({"model": "x", "input": "hi"}, 5.0)
    err = excinfo.value
    assert err.reason == "max_output_tokens"
    assert err.output_chars == len('{"ok": "cut')
    assert err.usage["reasoning_tokens"] == 60
    assert FAKE_KEY not in str(err)


def test_usage_includes_reasoning_tokens(configured, monkeypatch) -> None:
    _stub(monkeypatch, [
        _payload('{"ok": "yes"}',
                 usage={"input_tokens": 7, "output_tokens": 5, "reasoning_tokens": 60}),
    ])
    _, usage = responses_structured(schema_name="Probe", schema=SCHEMA, input_text="hi")
    assert usage == {"input_tokens": 7, "output_tokens": 5, "reasoning_tokens": 60}
