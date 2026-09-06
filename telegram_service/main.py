"""
Microsserviço do Telegram -- processo separado, mesmo padrão de
litellm_proxy/ e mcp_server/. Duas direções:

  POST /webhook  -- Telegram -> aqui -- mensagem de um lead chegando
  POST /send     -- sdr-bot-api -> aqui -- resposta de um humano (via
                    TelegramDeliveryAdapter) saindo

Todo conhecimento específico do Telegram (bot token, verificação de
secret, formato da API) fica encapsulado aqui -- sdr-bot-api nunca
chama a API do Telegram diretamente, só este serviço via HTTP simples
(mesmo padrão do whatsapp_service, ainda não implementado, mas já
referenciado em app/handoff/delivery.py).
"""

import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# find_dotenv() (chamado por load_dotenv() por baixo) busca a partir
# deste arquivo pra cima -- encontra o .env na raiz do repo mesmo
# telegram_service rodando com cwd/venv próprios (make telegram-up faz
# `cd telegram_service` antes de subir o uvicorn).
load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
SDR_BOT_API_URL = os.environ.get("SDR_BOT_API_URL", "http://localhost:8001")

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = FastAPI(title="Telegram Service", version="0.1.0")


class SendRequest(BaseModel):
    user_id: str
    session_id: str
    text: str


def _chat_id_from_user_id(user_id: str) -> int:
    if not user_id.startswith("telegram:"):
        raise HTTPException(
            status_code=400, detail=f"user_id não é do canal telegram: {user_id!r}"
        )
    return int(user_id.removeprefix("telegram:"))


async def _send_telegram_message(chat_id: int, text: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{TELEGRAM_API_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        response.raise_for_status()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    payload: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    """Recebe um Update do Telegram. Verificação de secret_token é
    obrigatória -- sem ela, qualquer requisição pra essa URL pública
    seria aceita como se viesse do Telegram de verdade."""
    if x_telegram_bot_api_secret_token != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="secret_token inválido")

    message = payload.get("message")
    if message is None or "text" not in message:
        # Update sem mensagem de texto (ex: reação, edição, membro
        # entrando no grupo) -- ignora sem erro, Telegram espera 200
        # mesmo pra updates que não processamos.
        return {"status": "ignored"}

    chat_id = message["chat"]["id"]
    text = message["text"]
    user_id = f"telegram:{chat_id}"
    session_id = user_id  # uma sessão por chat -- suficiente pro escopo atual

    async with httpx.AsyncClient(timeout=30.0) as client:
        chat_response = await client.post(
            f"{SDR_BOT_API_URL}/chat",
            json={
                "user_id": user_id,
                "session_id": session_id,
                "message": text,
                "channel": "telegram",
            },
        )
        chat_response.raise_for_status()
        body = chat_response.json()

    # handoff_mode=True significa que um humano já assumiu -- o bot não
    # gerou resposta nova, não há nada pra entregar agora (a entrega da
    # resposta HUMANA acontece via /send, não aqui).
    if body.get("response"):
        await _send_telegram_message(chat_id, body["response"])

    return {"status": "ok"}


@app.post("/send")
async def send(payload: SendRequest) -> dict:
    """Chamado por TelegramDeliveryAdapter (app/handoff/delivery.py)
    quando um humano responde via /handoff/.../reply."""
    chat_id = _chat_id_from_user_id(payload.user_id)
    await _send_telegram_message(chat_id, payload.text)
    return {"status": "sent"}
