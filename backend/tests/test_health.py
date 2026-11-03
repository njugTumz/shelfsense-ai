"""Health endpoint tests. Run from backend/: python -m pytest."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok_without_secrets() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "field-backend"
    assert "env" in body
    lowered = response.text.lower()
    for forbidden in ("api_key", "apikey", "secret", "token", "begin ", "key="):
        assert forbidden not in lowered
