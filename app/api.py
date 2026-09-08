"""
Camada HTTP sobre o sistema multi-agente.

Existe principalmente para dar ao pipeline de CI/CD (e ao deploy na GCP)
algo real para buildar e servir — o fluxo de teste interativo continua
sendo app/main.py (CLI) enquanto o projeto evolui.

Handoff humano: /chat agora é "handoff-aware" -- checa
STATE_HANDOFF_MODE antes de rodar o agente. Isso vale pra QUALQUER
canal que chame /chat (widget web, whatsapp_service, futuro Telegram),
não só WhatsApp -- a decisão bot-vs-humano mora aqui, num lugar só, não
duplicada em cada adaptador de canal (ver app/handoff/delivery.py pro
racional completo).
"""

import asyncio
import os
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.adk.errors import StaleSessionError
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel

from app.agents import root_agent  # importar isso já carrega o .env (ver app/config/__init__.py)
from app.agents.knowledge import warmup_mcp_connection
from app.agents.session.state_schema import (
    STATE_CHANNEL,
    STATE_HANDOFF_CLAIMED_BY,
    STATE_HANDOFF_MODE,
)
from app.handoff.delivery import get_delivery_adapter
from app.session_service import get_or_create_session, get_session_service

APP_NAME = os.getenv("SDR_APP_NAME", "sdr-bot")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Pré-aquece a conexão MCP (ver warmup_mcp_connection) ANTES de
    # aceitar tráfego real -- move o custo de boot do subprocess pro
    # startup do container (que já tem startup-cpu-boost=true e um
    # STARTUP TCP probe esperando por ele), em vez de pagar esse custo
    # dentro da primeira requisição real de um usuário.
    await warmup_mcp_connection()
    yield


app = FastAPI(title="SDR Bot API", version="0.1.0", lifespan=_lifespan)

# HANDOFF_DASHBOARD_ORIGINS: essa API não tem CORS habilitado até aqui
# porque nenhum dos canais existentes (telegram_service, o CLI, o
# widget web futuro) é um browser chamando de uma origem diferente --
# telegram_service e o CLI rodam server-side, sem CORS envolvido. O
# painel de handoff (React, Parte 7) é o primeiro cliente que roda no
# browser do agente humano, numa origem diferente (ex: localhost:5173
# em dev) -- sem isso, toda chamada dele pra /sessions, /handoff/*
# seria bloqueada pelo próprio browser antes de chegar aqui. Lista
# vazia por padrão (nenhuma origem liberada) -- setar explicitamente
# no .env em vez de liberar "*", já que /handoff/*/reply e /claim não
# têm autenticação nenhuma hoje (ver docstring de claim_handoff).
_cors_origins = [
    origin.strip()
    for origin in os.getenv("HANDOFF_DASHBOARD_ORIGINS", "").split(",")
    if origin.strip()
]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

_session_service = get_session_service()
_runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=_session_service)


class ChatRequest(BaseModel):
    user_id: str
    session_id: str
    message: str
    channel: str = "web"  # setado na criação da sessão, ignorado depois


class ChatResponse(BaseModel):
    agent: str
    response: str
    handoff_mode: bool = False  # True = sem resposta do bot, um humano vai responder


class ClaimRequest(BaseModel):
    claimed_by: str | None = None


class ReplyRequest(BaseModel):
    text: str


class HistoryEvent(BaseModel):
    author: str
    text: str | None
    timestamp: float


class HistoryResponse(BaseModel):
    session_id: str
    handoff_mode: bool
    events: list[HistoryEvent]


class SessionSummary(BaseModel):
    user_id: str
    session_id: str
    channel: str | None = None
    handoff_mode: bool = False
    last_update_time: float


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/sessions", response_model=list[SessionSummary])
async def list_sessions() -> list[SessionSummary]:
    """Lista toda conversa existente (qualquer user_id/session_id) --
    é como se descobre QUAIS pares user_id/session_id existem, pra
    depois consultar /handoff/{user_id}/{session_id}/history de cada
    um. Existe porque, sem observabilidade de produção funcionando
    (ver app/agents/observability.py), não tem outro jeito de saber
    quem já falou com o bot.

    list_sessions() do ADK, sem user_id, já retorna TODAS as sessões
    de qualquer usuário -- não precisa de uma tabela paralela nem de
    tracing pra isso, é literalmente uma consulta na mesma tabela que
    guarda o histórico. Retorna sessões sem os `events` (mais leve,
    útil só pra listar "quem", não "o quê foi dito") -- reordenadas
    da mais recente pra mais antiga, já que numa lista de conversas
    isso importa mais que a ordem de criação usada internamente.
    """
    response = await _session_service.list_sessions(app_name=APP_NAME)
    summaries = [
        SessionSummary(
            user_id=session.user_id,
            session_id=session.id,
            channel=session.state.get(STATE_CHANNEL),
            handoff_mode=bool(session.state.get(STATE_HANDOFF_MODE)),
            last_update_time=session.last_update_time,
        )
        for session in response.sessions
    ]
    summaries.sort(key=lambda s: s.last_update_time, reverse=True)
    return summaries


_RESET_COMMAND = "/newsession"

# StaleSessionError acontece quando duas requisições pra MESMA sessão
# se sobrepõem -- ex: telegram_service dá timeout (30s) esperando uma
# resposta lenta (cold start, RAG), Telegram reenvia o mesmo update, e
# a tentativa original ainda está rodando em background quando a
# reenviada chega. A que perde a corrida tenta gravar um evento sobre
# um estado de sessão que já mudou, e o ADK rejeita (checagem de
# concorrência otimista) -- a própria mensagem de erro já diz o que
# fazer: recarregar a sessão e tentar de novo. Descoberto em produção
# de verdade: sem isso, a sessão ficava PERMANENTEMENTE corrompida (um
# evento de tool_call gravado sem o tool_result correspondente), e
# TODA mensagem seguinte nessa sessão falhava do mesmo jeito pra
# sempre, até alguém mandar /newsession manualmente.
_MAX_STALE_SESSION_RETRIES = 3


async def _backoff_before_retry(attempt: int) -> None:
    # Jitter aleatório (não backoff exponencial fixo) de propósito: o
    # cenário típico é DUAS requisições colidindo ao mesmo tempo -- um
    # delay determinístico faria as duas tentarem de novo no mesmo
    # instante, colidindo de novo.
    await asyncio.sleep(random.uniform(0.1, 0.4) * (attempt + 1))


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    # Fica aqui (não em telegram_service) pelo mesmo motivo do handoff
    # logo abaixo: a decisão de resetar uma conversa não é específica de
    # canal -- QUALQUER canal que chame /chat (web, WhatsApp, Telegram)
    # ganha isso de graça, num lugar só, em vez de cada adapter ter que
    # reimplementar "reconhecer esse comando". Checado ANTES do
    # handoff_mode de propósito: também serve pra um lead sair de uma
    # conversa em handoff (humano assumiu) e voltar a falar com o bot.
    if payload.message.strip().lower() == _RESET_COMMAND:
        await _session_service.delete_session(
            app_name=APP_NAME, user_id=payload.user_id, session_id=payload.session_id
        )
        await get_or_create_session(
            _session_service,
            app_name=APP_NAME,
            user_id=payload.user_id,
            session_id=payload.session_id,
            initial_state={STATE_CHANNEL: payload.channel},
        )
        return ChatResponse(
            agent="system",
            response="Nova conversa iniciada! Pode mandar sua mensagem.",
        )

    session = await get_or_create_session(
        _session_service,
        app_name=APP_NAME,
        user_id=payload.user_id,
        session_id=payload.session_id,
        initial_state={STATE_CHANNEL: payload.channel},
    )

    if session.state.get(STATE_HANDOFF_MODE):
        event = Event(
            author="user",
            content=types.Content(role="user", parts=[types.Part(text=payload.message)]),
            invocation_id=f"handoff-{payload.session_id}",
        )
        for attempt in range(_MAX_STALE_SESSION_RETRIES):
            try:
                await _session_service.append_event(session, event)
                break
            except StaleSessionError:
                if attempt == _MAX_STALE_SESSION_RETRIES - 1:
                    raise
                await _backoff_before_retry(attempt)
                # append_event precisa do Session OBJECT atualizado, não
                # só do session_id -- reusar o `session` antigo de novo
                # falharia com o mesmo erro.
                session = await _session_service.get_session(
                    app_name=APP_NAME, user_id=payload.user_id, session_id=payload.session_id
                )
        return ChatResponse(agent="human", response="", handoff_mode=True)

    content = types.Content(role="user", parts=[types.Part(text=payload.message)])

    agent_name = "OrchestratorAgent"
    response_text = ""

    for attempt in range(_MAX_STALE_SESSION_RETRIES):
        try:
            async for event in _runner.run_async(
                user_id=payload.user_id,
                session_id=payload.session_id,
                new_message=content,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    agent_name = event.author
                    response_text = event.content.parts[0].text
            break
        except StaleSessionError:
            if attempt == _MAX_STALE_SESSION_RETRIES - 1:
                raise
            # run_async recarrega a sessão do zero a cada chamada (não
            # reusa um objeto Session em memória) -- só repetir a
            # chamada já conta como "recarregar" no sentido que o ADK
            # pede. Seguro reenviar a MESMA new_message: se chegou a
            # cair aqui, o evento de usuário desta tentativa nunca foi
            # persistido de verdade (ver traceback real que motivou
            # isso: falhava em _append_user_event, o primeiro append da
            # chamada).
            agent_name = "OrchestratorAgent"
            response_text = ""
            await _backoff_before_retry(attempt)

    return ChatResponse(agent=agent_name, response=response_text)


@app.post("/handoff/{user_id}/{session_id}/claim")
async def claim_handoff(user_id: str, session_id: str, payload: ClaimRequest) -> dict:
    """Um humano assume a conversa -- a partir daqui, /chat pra essa
    sessão para de rodar o agente. Idempotente: chamar de novo só
    atualiza claimed_by, não é erro.

    Mutação de estado precisa ir via EventActions.state_delta -- mudar
    session.state diretamente e chamar append_event() com um Event
    sem state_delta NÃO persiste (confirmado rodando de verdade: o
    dict local muda, mas nada chega no banco)."""
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    event = Event(
        author="system",
        actions=EventActions(
            state_delta={
                STATE_HANDOFF_MODE: True,
                STATE_HANDOFF_CLAIMED_BY: payload.claimed_by,
            }
        ),
        invocation_id=f"handoff-claim-{session_id}",
    )
    await _session_service.append_event(session, event)

    return {"status": "claimed", "session_id": session_id, "claimed_by": payload.claimed_by}


@app.post("/handoff/{user_id}/{session_id}/release")
async def release_handoff(user_id: str, session_id: str) -> dict:
    """Devolve a conversa pro bot -- o outro lado do claim_handoff.
    Sem isso, a única forma de uma sessão SAIR do modo handoff era o
    próprio lead mandar /newsession (que também apaga a conversa) --
    não existia um jeito do humano dizer "terminei, bot pode
    continuar" sem resetar tudo. Idempotente pelo mesmo motivo de
    claim_handoff: chamar de novo numa sessão que já não está em
    handoff não é erro, só não muda nada."""
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    event = Event(
        author="system",
        actions=EventActions(
            state_delta={
                STATE_HANDOFF_MODE: False,
                STATE_HANDOFF_CLAIMED_BY: None,
            }
        ),
        invocation_id=f"handoff-release-{session_id}",
    )
    await _session_service.append_event(session, event)

    return {"status": "released", "session_id": session_id}


@app.post("/handoff/{user_id}/{session_id}/reply")
async def handoff_reply(user_id: str, session_id: str, payload: ReplyRequest) -> dict:
    """Um humano responde -- registra a mensagem na sessão E entrega
    pro canal de origem do lead (web, whatsapp, etc)."""
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if not session.state.get(STATE_HANDOFF_MODE):
        raise HTTPException(
            status_code=409,
            detail="Sessão não está em modo handoff -- chame /claim primeiro",
        )

    event = Event(
        author="human",
        content=types.Content(role="model", parts=[types.Part(text=payload.text)]),
        invocation_id=f"handoff-reply-{session_id}",
    )
    await _session_service.append_event(session, event)

    channel = session.state.get(STATE_CHANNEL)
    delivery = get_delivery_adapter(channel)
    await delivery.deliver(user_id=user_id, session_id=session_id, text=payload.text)

    return {"status": "delivered", "channel": channel}


def _extract_event_text(event: Event) -> str | None:
    """Extrai um texto legível de um evento -- nem todo evento tem
    .text direto: tool calls têm function_call, tool results têm
    function_response, e eventos de sistema (ex: o que claim_handoff
    gera, só com state_delta) podem não ter content nenhum."""
    if event.content is None or not event.content.parts:
        return None

    texts = []
    for part in event.content.parts:
        if part.text:
            texts.append(part.text)
        elif part.function_call:
            texts.append(f"[chamou {part.function_call.name}]")
        elif part.function_response:
            texts.append(f"[resposta de {part.function_response.name}]")

    return " ".join(texts) if texts else None


@app.get("/handoff/{user_id}/{session_id}/history", response_model=HistoryResponse)
async def get_history(user_id: str, session_id: str) -> HistoryResponse:
    """Histórico completo de uma conversa -- pré-requisito pra QUALQUER
    fluxo de handoff de verdade: um humano precisa ver o que o lead já
    disse antes de decidir se vale a pena assumir (/claim), e um
    dashboard futuro precisa disso pra renderizar a conversa inteira,
    não só as mensagens a partir do momento em que foi aberto."""
    session = await _session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")

    events = [
        HistoryEvent(author=e.author, text=_extract_event_text(e), timestamp=e.timestamp)
        for e in session.events
    ]

    return HistoryResponse(
        session_id=session_id,
        handoff_mode=bool(session.state.get(STATE_HANDOFF_MODE)),
        events=events,
    )
