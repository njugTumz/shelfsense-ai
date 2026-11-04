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
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.headers = headers or {}

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

    def fake_post(url, headers=None, json=None, files=None, timeout=None):
        calls.append(json)
        return _Resp(payloads[min(len(calls) - 1, len(payloads) - 1)])

    monkeypatch.setattr(sc.httpx, "post", fake_post)
    return calls


def _stub_responses(monkeypatch, responses):
    """Stub with full _Resp objects (status codes / headers vary per call)."""
    calls = []

    def fake_post(url, headers=None, json=None, files=None, timeout=None):
        calls.append(json)
        resp = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(resp, Exception):
            raise resp
        return resp

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


def test_transport_timeout_splits_connect_and_read(configured, monkeypatch) -> None:
    seen = {}

    def fake_post(url, headers=None, json=None, files=None, timeout=None):
        seen["timeout"] = timeout
        return _Resp(_payload('{"ok": "yes"}'))

    monkeypatch.setattr(sc.httpx, "post", fake_post)
    responses_structured(schema_name="Probe", schema=SCHEMA, input_text="hi",
                         timeout_s=600.0)
    transport = seen["timeout"]
    assert transport.connect == sc.CONNECT_TIMEOUT_S == 10.0
    assert transport.read == 600.0
    assert transport.write == sc.WRITE_TIMEOUT_S
    assert transport.pool == sc.POOL_TIMEOUT_S


def test_call_logs_timing_usage_and_outcome(configured, monkeypatch, caplog) -> None:
    _stub(monkeypatch, [_payload('{"ok": "yes"}')])
    with caplog.at_level("INFO", logger="field.spark"):
        responses_structured(schema_name="Probe", schema=SCHEMA,
                             input_text="SECRET-PROMPT-MUST-NOT-LEAK",
                             effort="high", max_output_tokens=100, timeout_s=600.0)
    lines = [r.message for r in caplog.records if r.name == "field.spark"]
    assert len(lines) == 1, lines
    line = lines[0]
    assert "site=Probe" in line and "effort=high" in line
    assert "timeout_s=600" in line and "outcome=ok" in line
    assert "input_tokens=10" in line and "output_tokens=5" in line
    assert "SECRET-PROMPT-MUST-NOT-LEAK" not in line
    assert FAKE_KEY not in line


def test_call_logs_error_outcome_on_transport_failure(configured, monkeypatch, caplog) -> None:
    import httpx

    def boom(url, headers=None, json=None, files=None, timeout=None):
        raise httpx.ReadTimeout("slow peer")

    monkeypatch.setattr(sc.httpx, "post", boom)
    monkeypatch.setattr(sc, "_sleep", lambda s: None)
    with caplog.at_level("INFO", logger="field.spark"):
        with pytest.raises(SparkError, match="TRANSPORT ERROR"):
            responses_structured(schema_name="Probe", schema=SCHEMA, input_text="hi")
    lines = [r.message for r in caplog.records
             if r.name == "field.spark" and r.message.startswith("spark_call ")]
    assert len(lines) == 1, lines
    assert "outcome=SparkTransientError" in lines[0]
    retries = [r.message for r in caplog.records
               if r.name == "field.spark" and "spark_retry" in r.message]
    assert len(retries) == 2, retries


def test_httpx_errors_classify_transient(configured, monkeypatch) -> None:
    import httpx

    from app.services.spark_client import SparkTransientError

    for exc in (
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.WriteTimeout("stuck"),
        httpx.PoolTimeout("exhausted"),
        httpx.RemoteProtocolError("dropped"),
    ):
        calls = _stub_responses(monkeypatch, [exc])
        with pytest.raises(SparkTransientError, match="TRANSPORT ERROR"):
            sc._post_responses_once({"model": "x"}, 5.0)
        assert len(calls) == 1


def test_http_status_classification(configured, monkeypatch) -> None:
    from app.services.spark_client import (
        SparkAuthError,
        SparkBadRequestError,
        SparkRateLimitError,
        SparkTransientError,
    )

    cases = [
        (408, SparkTransientError, None),
        (429, SparkRateLimitError, None),
        (500, SparkTransientError, None),
        (503, SparkTransientError, None),
        (401, SparkAuthError, None),
        (403, SparkAuthError, None),
        (400, SparkBadRequestError, None),
        (404, SparkBadRequestError, None),
        (422, SparkBadRequestError, None),
    ]
    for status, cls, _ in cases:
        _stub_responses(monkeypatch, [_Resp({"error": "x"}, status_code=status)])
        with pytest.raises(cls):
            sc._post_responses_once({"model": "x"}, 5.0)


def test_retry_after_parsing() -> None:
    import time
    from email.utils import formatdate

    assert sc._parse_retry_after({"Retry-After": "30"}) == 30.0
    assert sc._parse_retry_after({"retry-after": "0"}) == 0.0
    assert sc._parse_retry_after({}) is None
    assert sc._parse_retry_after({"Retry-After": "garbage"}) is None
    future = formatdate(time.time() + 45, usegmt=True)
    parsed = sc._parse_retry_after({"Retry-After": future})
    assert parsed is not None and 40.0 < parsed <= 45.0


def test_rate_limit_carries_retry_after(configured, monkeypatch) -> None:
    from app.services.spark_client import SparkRateLimitError

    _stub_responses(monkeypatch, [
        _Resp({"error": "slow down"}, status_code=429, headers={"Retry-After": "12"}),
    ])
    with pytest.raises(SparkRateLimitError) as excinfo:
        sc._post_responses_once({"model": "x"}, 5.0)
    assert excinfo.value.retry_after_s == 12.0
    assert isinstance(excinfo.value, sc.SparkTransientError)


def test_retry_delay_bounds_and_floor() -> None:
    for _ in range(50):
        assert 1.0 <= sc.retry_delay_s(1) <= 2.0
        assert 2.0 <= sc.retry_delay_s(2) <= 4.0
        assert 30.0 <= sc.retry_delay_s(10) <= 60.0
        assert sc.retry_delay_s(1, retry_after_s=120.0) >= 120.0


def test_transient_retries_then_succeeds(configured, monkeypatch) -> None:
    import httpx

    sleeps = []
    monkeypatch.setattr(sc, "_sleep", sleeps.append)
    calls = _stub_responses(monkeypatch, [
        httpx.ReadTimeout("slow"),
        _Resp({"error": "busy"}, status_code=503),
        _Resp(_payload('{"ok": "yes"}')),
    ])
    data, _ = sc._post_responses({"model": "x"}, 5.0, site="Probe")
    assert data == {"ok": "yes"}
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 2.0
    assert 2.0 <= sleeps[1] <= 4.0


def test_transient_gives_up_after_three_attempts(configured, monkeypatch) -> None:
    import httpx

    from app.services.spark_client import SparkTransientError

    sleeps = []
    monkeypatch.setattr(sc, "_sleep", sleeps.append)
    calls = _stub_responses(monkeypatch, [httpx.ConnectError("down")])
    with pytest.raises(SparkTransientError):
        sc._post_responses({"model": "x"}, 5.0, site="Probe")
    assert len(calls) == 3
    assert len(sleeps) == 2


def test_non_transient_never_retried(configured, monkeypatch) -> None:
    from app.services.spark_client import SparkAuthError, SparkBadRequestError

    sleeps = []
    monkeypatch.setattr(sc, "_sleep", sleeps.append)
    calls = _stub_responses(monkeypatch, [_Resp({"e": "x"}, status_code=401)])
    with pytest.raises(SparkAuthError):
        sc._post_responses({"model": "x"}, 5.0, site="Probe")
    assert len(calls) == 1
    assert sleeps == []

    calls = _stub_responses(monkeypatch, [_Resp({"e": "x"}, status_code=422)])
    with pytest.raises(SparkBadRequestError):
        sc._post_responses({"model": "x"}, 5.0, site="Probe")
    assert len(calls) == 1
    assert sleeps == []


def test_truncation_not_transport_retried(configured, monkeypatch) -> None:
    calls = _stub_responses(monkeypatch, [
        _Resp(_payload('{"ok": "cut', status="incomplete", reason="max_output_tokens")),
    ])
    with pytest.raises(SparkTruncatedError):
        sc._post_responses({"model": "x"}, 5.0, site="Probe")
    assert len(calls) == 1


def test_retry_log_names_site_and_class(configured, monkeypatch, caplog) -> None:
    import httpx

    monkeypatch.setattr(sc, "_sleep", lambda s: None)
    _stub_responses(monkeypatch, [
        httpx.ReadTimeout("slow"),
        _Resp(_payload('{"ok": "yes"}')),
    ])
    with caplog.at_level("WARNING", logger="field.spark"):
        sc._post_responses({"model": "x"}, 5.0, site="Probe")
    retries = [r.message for r in caplog.records
               if r.name == "field.spark" and "spark_retry" in r.message]
    assert len(retries) == 1, retries
    assert "site=Probe" in retries[0] and "SparkTransientError" in retries[0]
    assert FAKE_KEY not in retries[0]
