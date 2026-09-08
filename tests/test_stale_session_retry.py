"""
Testes do retry em StaleSessionError em app/api.py -- mesmo padrão de
test_handoff.py (SQLite real via SESSION_DB_URL, Runner mockado).

StaleSessionError acontece quando duas requisições pra mesma sessão se
sobrepõem (ex: Telegram reenviando um update enquanto a tentativa
original ainda está em voo) -- sem o retry, a sessão ficava
permanentemente corrompida (ver commit que introduziu isso). Estes
testes mockam StaleSessionError diretamente (não reproduzem a
concorrência real) -- o que importa aqui é só a lógica de retry em si.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from google.adk.errors import StaleSessionError
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


def _make_flaky_run_async(fail_times: int, text: str = "resposta do bot"):
    """Levanta StaleSessionError nas primeiras `fail_times` chamadas,
    depois sucede normalmente -- simula a corrida real ser resolvida
    depois de algumas tentativas."""
    calls = {"n": 0}

    async def _flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise StaleSessionError("stale")
        yield _fake_bot_event(text)

    return _flaky, calls


def test_chat_retries_and_succeeds_after_transient_stale_session(client):
    test_client, api_module = client

    flaky_run_async, calls = _make_flaky_run_async(fail_times=1)
    with patch.object(api_module._runner, "run_async", flaky_run_async):
        response = test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "resposta do bot"
    assert calls["n"] == 2  # falhou 1x, teve sucesso na 2a tentativa


def test_chat_gives_up_after_max_retries(client):
    test_client, api_module = client
    # raise_server_exceptions=False: por padrão o TestClient RE-LEVANTA
    # exceções não tratadas em vez de convertê-las numa resposta 500 --
    # aqui queremos testar exatamente essa conversão (o comportamento
    # real de produção via FastAPI), não deixar o teste falhar com a
    # exceção crua.
    test_client = TestClient(api_module.app, raise_server_exceptions=False)

    flaky_run_async, calls = _make_flaky_run_async(fail_times=99)
    with patch.object(api_module._runner, "run_async", flaky_run_async):
        response = test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"}
        )

    assert response.status_code == 500
    assert calls["n"] == api_module._MAX_STALE_SESSION_RETRIES


def test_handoff_reply_retries_and_succeeds_after_transient_stale_session(client):
    test_client, api_module = client

    async def _fake_run_async(*args, **kwargs):
        yield _fake_bot_event()

    with patch.object(api_module._runner, "run_async", _fake_run_async):
        test_client.post("/chat", json={"user_id": "u1", "session_id": "s1", "message": "oi"})
    test_client.post("/handoff/u1/s1/claim", json={"claimed_by": "agente-joao"})

    append_calls = {"n": 0}
    real_append_event = api_module._session_service.append_event

    async def _flaky_append_event(session, event):
        append_calls["n"] += 1
        if append_calls["n"] == 1:
            raise StaleSessionError("stale")
        return await real_append_event(session, event)

    with patch.object(api_module._session_service, "append_event", _flaky_append_event):
        response = test_client.post(
            "/chat", json={"user_id": "u1", "session_id": "s1", "message": "segunda mensagem"}
        )

    assert response.status_code == 200
    assert response.json()["handoff_mode"] is True
    assert append_calls["n"] == 2
