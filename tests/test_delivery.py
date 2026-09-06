"""
Testes unitários dos adaptadores de entrega (app/handoff/delivery.py)
-- ao contrário de test_handoff.py (que testa o fluxo via TestClient),
aqui o alvo é só o cliente HTTP de cada adaptador: URL certa, payload
certo, sem depender do resto da API.
"""

from unittest.mock import AsyncMock, patch

from app.handoff.delivery import TelegramDeliveryAdapter


async def test_telegram_delivery_adapter_calls_correct_url_and_payload():
    adapter = TelegramDeliveryAdapter("http://localhost:8200")

    with patch("app.handoff.delivery.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_response = AsyncMock()
        mock_response.raise_for_status = lambda: None
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        await adapter.deliver(user_id="telegram:123", session_id="telegram:123", text="oi")

        mock_client.post.assert_awaited_once_with(
            "http://localhost:8200/send",
            json={"user_id": "telegram:123", "session_id": "telegram:123", "text": "oi"},
        )
