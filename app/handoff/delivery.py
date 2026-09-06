"""
Porta de entrega de mensagens de volta pro lead -- abstrai "como uma
resposta (do bot ou de um humano) chega no canal de origem", que depende
de STATE_CHANNEL da sessão (ver app/agents/session/state_schema.py).

Canal "web" é no-op: quem chamou POST /chat recebe a resposta síncrona
no próprio corpo do request HTTP, e o histórico da sessão já guarda a
resposta de um handoff -- não há canal de push externo pra web ainda.
"""

import os
from abc import ABC, abstractmethod

import httpx


class MessageDeliveryPort(ABC):
    @abstractmethod
    async def deliver(self, *, user_id: str, session_id: str, text: str) -> None:
        """Entrega `text` de volta pro lead através do canal que esse
        adaptador representa. Levanta exceção se a entrega falhar --
        quem chama decide como tratar isso."""
        ...


class NoOpDeliveryAdapter(MessageDeliveryPort):
    """Canal 'web' -- resposta já fica disponível no histórico da sessão."""

    async def deliver(self, *, user_id: str, session_id: str, text: str) -> None:
        return None


class WhatsAppDeliveryAdapter(MessageDeliveryPort):
    """Chama o whatsapp_service (processo separado) pra entregar via
    WhatsApp Business API."""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def deliver(self, *, user_id: str, session_id: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/send",
                json={"user_id": user_id, "session_id": session_id, "text": text},
            )
            response.raise_for_status()


class TelegramDeliveryAdapter(MessageDeliveryPort):
    """Chama o telegram_service (processo separado) -- mesmo padrão do
    WhatsAppDeliveryAdapter. Nenhum conhecimento da API do Telegram
    mora aqui, só um contrato HTTP simples."""

    def __init__(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    async def deliver(self, *, user_id: str, session_id: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/send",
                json={"user_id": user_id, "session_id": session_id, "text": text},
            )
            response.raise_for_status()


def get_delivery_adapter(channel: str | None) -> MessageDeliveryPort:
    if channel == "whatsapp":
        whatsapp_url = os.environ.get("WHATSAPP_SERVICE_URL")
        if not whatsapp_url:
            raise RuntimeError(
                "WHATSAPP_SERVICE_URL não configurado -- necessário pra "
                "entregar respostas de handoff no canal 'whatsapp'."
            )
        return WhatsAppDeliveryAdapter(whatsapp_url)
    if channel == "telegram":
        telegram_url = os.environ.get("TELEGRAM_SERVICE_URL")
        if not telegram_url:
            raise RuntimeError(
                "TELEGRAM_SERVICE_URL não configurado -- necessário pra "
                "entregar respostas de handoff no canal 'telegram'."
            )
        return TelegramDeliveryAdapter(telegram_url)
    return NoOpDeliveryAdapter()
