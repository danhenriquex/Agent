"""
Testes do fluxo de handoff humano em app/api.py -- usa SQLite real
(via SESSION_DB_URL) e o Runner mockado (não precisa de LLM), mas o
SessionService, os endpoints, e a lógica de state_delta são todos
reais. O que importa aqui é exatamente o tipo de bug que um teste
superficial esconderia: mutação de estado que parece funcionar no
objeto Python mas não persiste (ver claim_handoff em app/api.py --
esse bug real foi encontrado escrevendo estes testes).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from google.adk.events import Event
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


def test_chat_runs_bot_normally_before_claim(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        response = test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["handoff_mode"] is False
    assert body["response"] == "resposta do bot"


def test_claim_marks_session_in_handoff_mode(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post("/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"})

    claim_response = test_client.post("/handoff/u1/s1/claim", json={"claimed_by": "agente-joao"})

    assert claim_response.status_code == 200
    assert claim_response.json()["status"] == "claimed"


def test_chat_does_not_run_bot_after_claim(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post("/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"})

    test_client.post("/handoff/u1/s1/claim", json={"claimed_by": "agente-joao"})

    bot_was_called = AsyncMock(side_effect=AssertionError("bot não deveria rodar após claim"))
    with patch.object(api_module._runner, "run_async", bot_was_called):
        response = test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "segunda mensagem"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["handoff_mode"] is True
    assert body["response"] == ""
    bot_was_called.assert_not_called()


def test_reply_requires_claim_first(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post("/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"})

    response = test_client.post("/handoff/u1/s1/reply", json={"text": "resposta humana"})

    assert response.status_code == 409


def test_reply_delivers_via_correct_channel(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post(
            "/chat",
            json={"user_id": "u1", "session_id": "s1", "message": "oi", "channel": "web"},
        )

    test_client.post("/handoff/u1/s1/claim", json={"claimed_by": "agente-joao"})

    with patch("app.api.get_delivery_adapter") as mock_get_adapter:
        mock_adapter = AsyncMock()
        mock_get_adapter.return_value = mock_adapter

        response = test_client.post("/handoff/u1/s1/reply", json={"text": "resposta humana"})

    assert response.status_code == 200
    mock_get_adapter.assert_called_once_with("web")
    mock_adapter.deliver.assert_awaited_once_with(
        user_id="u1", session_id="s1", text="resposta humana"
    )


def test_claim_nonexistent_session_returns_404(client):
    test_client, _api_module = client

    response = test_client.post("/handoff/u1/sessao-que-nao-existe/claim", json={})

    assert response.status_code == 404


async def test_history_returns_events_in_order(client):
    test_client, api_module = client

    session = await api_module._session_service.create_session(
        app_name=api_module.APP_NAME, user_id="u1", session_id="s1"
    )
    await api_module._session_service.append_event(
        session,
        Event(author="user", content=types.Content(role="user", parts=[types.Part(text="oi")])),
    )
    await api_module._session_service.append_event(
        session,
        Event(
            author="OrchestratorAgent",
            content=types.Content(
                role="model", parts=[types.Part(text="oi! como posso ajudar?")]
            ),
        ),
    )

    response = test_client.get("/handoff/u1/s1/history")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s1"
    assert body["handoff_mode"] is False
    authors = [e["author"] for e in body["events"]]
    assert authors == ["user", "OrchestratorAgent"]


def test_history_reflects_handoff_mode(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post("/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"})
    test_client.post("/handoff/u1/s1/claim", json={"claimed_by": "agente-joao"})

    response = test_client.get("/handoff/u1/s1/history")

    assert response.json()["handoff_mode"] is True


def test_history_handles_events_without_content(client):
    test_client, api_module = client

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post("/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"})
    test_client.post("/handoff/u1/s1/claim", json={"claimed_by": "agente-joao"})

    response = test_client.get("/handoff/u1/s1/history")

    events = response.json()["events"]
    system_events = [e for e in events if e["author"] == "system"]
    assert len(system_events) == 1
    assert system_events[0]["text"] is None


def test_history_nonexistent_session_returns_404(client):
    test_client, _api_module = client

    response = test_client.get("/handoff/u1/sessao-que-nao-existe/history")

    assert response.status_code == 404
