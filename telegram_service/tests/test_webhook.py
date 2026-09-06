"""
Testes do webhook do Telegram -- payload real (confirmado contra a
documentação oficial da Bot API: message.chat.id, message.text),
chamadas HTTP mockadas (sdr-bot-api e a API do Telegram), verificação
de secret_token testada explicitamente (sem isso, qualquer requisição
pública seria aceita).
"""

import os
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")

from telegram_service.main import app  # noqa: E402

client = TestClient(app)

REAL_TELEGRAM_UPDATE = {
    "update_id": 123456789,
    "message": {
        "message_id": 1,
        "chat": {"id": 987654321, "type": "private"},
        "from": {"id": 987654321, "is_bot": False, "first_name": "Lead"},
        "text": "oi, quanto custa?",
        "date": 1735689600,
    },
}


def test_webhook_rejects_missing_secret_token():
    response = client.post("/webhook", json=REAL_TELEGRAM_UPDATE)

    assert response.status_code == 403


def test_webhook_rejects_wrong_secret_token():
    response = client.post(
        "/webhook",
        json=REAL_TELEGRAM_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": "token-errado"},
    )

    assert response.status_code == 403


def test_webhook_forwards_to_chat_and_replies_via_telegram():
    with (
        patch("telegram_service.main.httpx.AsyncClient") as mock_client_cls,
        patch("telegram_service.main._send_telegram_message") as mock_send,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_response = AsyncMock()
        mock_response.json = lambda: {
            "agent": "OrchestratorAgent",
            "response": "Starter custa R$12/mês",
            "handoff_mode": False,
        }
        mock_response.raise_for_status = lambda: None
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/webhook",
            json=REAL_TELEGRAM_UPDATE,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        )

        assert response.status_code == 200
        # user_id/session_id derivados corretamente do chat_id real
        called_payload = mock_client.post.call_args.kwargs["json"]
        assert called_payload["user_id"] == "telegram:987654321"
        assert called_payload["channel"] == "telegram"
        mock_send.assert_awaited_once_with(987654321, "Starter custa R$12/mês")


def test_webhook_ignores_updates_without_text_message():
    non_text_update = {"update_id": 1, "my_chat_member": {"chat": {"id": 1}}}

    response = client.post(
        "/webhook",
        json=non_text_update,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_send_delivers_to_correct_chat_id():
    with patch("telegram_service.main._send_telegram_message") as mock_send:
        response = client.post(
            "/send",
            json={
                "user_id": "telegram:987654321",
                "session_id": "telegram:987654321",
                "text": "resposta do humano",
            },
        )

        assert response.status_code == 200
        mock_send.assert_awaited_once_with(987654321, "resposta do humano")


def test_send_rejects_non_telegram_user_id():
    response = client.post(
        "/send",
        json={"user_id": "web:abc", "session_id": "s1", "text": "oi"},
    )

    assert response.status_code == 400
