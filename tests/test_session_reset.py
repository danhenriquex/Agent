"""
Testes do comando /newsession em app/api.py -- mesmo padrão de
test_handoff.py (SQLite real via SESSION_DB_URL, Runner mockado, sem
custo de LLM): o branch de reset retorna ANTES de chamar o Runner, então
é 100% determinístico.
"""

from unittest.mock import AsyncMock, patch

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


def test_newsession_does_not_run_the_bot(client):
    test_client, api_module = client

    bot_was_called = AsyncMock(side_effect=AssertionError("/newsession não deveria rodar o bot"))
    with patch.object(api_module._runner, "run_async", bot_was_called):
        response = test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "/newsession"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "system"
    assert body["handoff_mode"] is False
    bot_was_called.assert_not_called()


def test_newsession_clears_prior_history(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post("/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"})
        test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "/newsession"}
        )

    response = test_client.get("/handoff/u1/s1/history")

    assert response.status_code == 200
    assert response.json()["events"] == []


def test_newsession_exits_handoff_mode(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post("/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"})
    test_client.post("/handoff/u1/s1/claim", json={"claimed_by": "agente-joao"})

    test_client.post(
        "/chat", json={"user_id": "u1", "session_id": "s1", "message": "/newsession"}
    )

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        response = test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi de novo"}
        )

    assert response.json()["handoff_mode"] is False
    assert response.json()["response"] == "resposta do bot"


def test_newsession_as_first_message_ever_does_not_error(client):
    test_client, _api_module = client

    response = test_client.post(
        "/chat", json={"user_id": "u1", "session_id": "sessao-nova", "message": "/newsession"}
    )

    assert response.status_code == 200


def test_newsession_is_case_and_whitespace_insensitive(client):
    test_client, api_module = client

    bot_was_called = AsyncMock(side_effect=AssertionError("/newsession não deveria rodar o bot"))
    with patch.object(api_module._runner, "run_async", bot_was_called):
        response = test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "  /NewSession  "}
        )

    assert response.status_code == 200
    assert response.json()["agent"] == "system"
    bot_was_called.assert_not_called()
