"""
Camada HTTP sobre o sistema multi-agente.

Existe principalmente para dar ao pipeline de CI/CD (e ao deploy na GCP)
algo real para buildar e servir — o fluxo de teste interativo continua
sendo app/main.py (CLI) enquanto o projeto evolui.

TODO Parte 5: instrumentar esta camada com LangFuse (tracing por request,
custo por conversa).
TODO: trocar InMemorySessionService por um session service persistente
(Redis, já previsto na arquitetura original) antes de qualquer uso real —
sessão em memória não sobrevive a um restart/autoscaling do Cloud Run
(cada revisão/instância teria seu próprio estado isolado).
"""

import os

from fastapi import FastAPI
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from app.agents import (
    root_agent,  # importar isso já carrega o .env (ver app/config/__init__.py)
)

APP_NAME = os.getenv("SDR_APP_NAME", "sdr-bot")

app = FastAPI(title="SDR Bot API", version="0.1.0")

_session_service = InMemorySessionService()
_runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=_session_service)
_known_sessions: set[tuple[str, str]] = set()


class ChatRequest(BaseModel):
    user_id: str
    session_id: str
    message: str


class ChatResponse(BaseModel):
    agent: str
    response: str


@app.get("/health")
async def health() -> dict:
    # Usado pelo Cloud Run (liveness/readiness probe) e pelo job de test
    # do CI/CD — não faz nenhuma chamada de LLM, só confirma que o
    # processo subiu e a hierarquia de agentes foi construída sem erro.
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    session_key = (payload.user_id, payload.session_id)
    if session_key not in _known_sessions:
        await _session_service.create_session(
            app_name=APP_NAME,
            user_id=payload.user_id,
            session_id=payload.session_id,
        )
        _known_sessions.add(session_key)

    content = types.Content(role="user", parts=[types.Part(text=payload.message)])

    agent_name = "OrchestratorAgent"
    response_text = ""

    async for event in _runner.run_async(
        user_id=payload.user_id,
        session_id=payload.session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            agent_name = event.author
            response_text = event.content.parts[0].text

    return ChatResponse(agent=agent_name, response=response_text)
