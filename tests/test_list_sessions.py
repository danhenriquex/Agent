"""
Testes do endpoint /sessions em app/api.py -- mesmo padrão de
test_handoff.py (SQLite real via SESSION_DB_URL, Runner mockado, sem
custo de LLM).
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from google.genai import types


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_DB_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("PII_HASH_SALT", "test-salt-not-for-production-use-32b")

    import importlib

    import app.api as api_module

    importlib.reload(api_module)

    return TestClient(api_module.app), api_module


def _fake_bot_event(text: str = "resposta do bot"):
    class FakeEvent:
        author = "OrchestratorAgent"
        content = types.Content(role="model", parts=[types.Part(text=text)])

        def is_final_response(self):
            return True

    return FakeEvent()


async def _fake_run_async(*args, **kwargs):
    yield _fake_bot_event()


def test_list_sessions_returns_every_user(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post(
            "/chat",
            json={
                "user_id": "telegram:1",
                "session_id": "telegram:1",
                "message": "oi",
                "channel": "telegram",
            },
        )
        test_client.post(
            "/chat",
            json={"user_id": "web:2", "session_id": "web:2", "message": "oi", "channel": "web"},
        )

    response = test_client.get("/sessions")

    assert response.status_code == 200
    body = response.json()
    user_ids = {s["user_id"] for s in body}
    assert user_ids == {"telegram:1", "web:2"}


def test_list_sessions_reflects_channel_and_handoff_mode(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post(
            "/chat",
            json={
                "user_id": "telegram:1",
                "session_id": "telegram:1",
                "message": "oi",
                "channel": "telegram",
            },
        )
    test_client.post("/handoff/telegram:1/telegram:1/claim", json={"claimed_by": "agente-joao"})

    response = test_client.get("/sessions")

    session = next(s for s in response.json() if s["user_id"] == "telegram:1")
    assert session["channel"] == "telegram"
    assert session["handoff_mode"] is True


def test_list_sessions_orders_most_recent_first(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "primeira"}
        )
        test_client.post(
            "/chat", json={"user_id": "u2", "session_id": "s2", "message": "segunda"}
        )

    response = test_client.get("/sessions")

    body = response.json()
    assert [s["user_id"] for s in body[:2]] == ["u2", "u1"]


def test_list_sessions_empty_when_no_conversations_yet(client):
    test_client, _api_module = client

    response = test_client.get("/sessions")

    assert response.status_code == 200
    assert response.json() == []
