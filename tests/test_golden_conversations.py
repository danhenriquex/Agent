"""
Layer 3 de testes: "conversas douradas" -- rodam contra o LLM de
verdade (via LiteLLM Proxy -> OpenRouter) e verificam ESTRUTURA (qual
tool foi chamada, o que foi escrito em session.state), não texto exato.
Isso é deliberadamente MAIS simples que o eval set da Parte 6 (sem
modelo-juiz, sem rubrica de nota) -- o objetivo aqui é pegar "um ajuste
de prompt fez o agente parar de chamar set_qualification_status", não
avaliar qualidade de resposta.

Custam chamadas reais de API e precisam do LiteLLM Proxy rodando --
por isso ficam FORA do `pytest`/`make test` padrão, rodando só quando
explicitamente pedido:

    make test-live
    # ou
    RUN_LIVE_TESTS=1 uv run pytest tests/test_golden_conversations.py -v

Pré-requisito: proxy de pé (`make proxy-up`) e .env configurado
(OPENROUTER_API_KEY, PII_HASH_SALT).
"""

import os
import uuid

import pytest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason=(
        "Testes ao vivo -- custam chamadas reais de API e precisam do "
        "LiteLLM Proxy rodando. Rode com RUN_LIVE_TESTS=1 (ou `make test-live`)."
    ),
)

_APP_NAME = "sdr-bot-test"


async def _run_conversation(agent, messages: list[str]) -> tuple[dict, str]:
    """Roda uma conversa multi-turno contra um agente real (specialist
    OU root_agent) e retorna (session.state final, texto da última
    resposta).

    Cada teste usa um session_id novo (uuid) -- sessões não devem
    vazar estado entre testes.
    """
    session_service = InMemorySessionService()
    user_id = "test-user"
    session_id = f"golden-{uuid.uuid4().hex[:8]}"

    await session_service.create_session(
        app_name=_APP_NAME, user_id=user_id, session_id=session_id
    )
    runner = Runner(agent=agent, app_name=_APP_NAME, session_service=session_service)

    last_response_text = ""
    for message in messages:
        content = types.Content(role="user", parts=[types.Part(text=message)])
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                last_response_text = event.content.parts[0].text or last_response_text

    session = await session_service.get_session(
        app_name=_APP_NAME, user_id=user_id, session_id=session_id
    )
    return session.state, last_response_text


# --- Orchestrator: saudação (padrão booking-first) ---


async def test_greeting_introduces_bot_and_company():
    from app.agents import root_agent
    from app.agents.persona import COMPANY_NAME

    _state, response_text = await _run_conversation(root_agent, ["oi"])

    # A queixa original era literal: "oi" respondia só "como posso
    # ajudar", sem contexto nenhum. Isso verifica a correção de forma
    # estrutural (nome da empresa presente), não texto exato -- resiste
    # a ajustes futuros de tom/redação do prompt.
    assert (
        COMPANY_NAME.lower() in response_text.lower()
    ), f"Resposta à saudação não menciona {COMPANY_NAME}: {response_text!r}"


# --- QualificationAgent ---


async def test_qualification_flow_sets_status():
    from app.agents.qualification import qualification_agent
    from app.agents.session.state_schema import STATE_QUALIFICATION_STATUS

    conversation = [
        "Oi, vi vocês no LinkedIn",
        "Temos uns 50 funcionários, orçamento de uns R$5k/mês",
        "Sou eu quem decide isso",
        "Precisamos resolver isso ainda esse trimestre",
    ]

    # Retry limitado -- BerriAI/litellm tem um bug documentado e ainda
    # não confirmado como corrigido pro nosso caminho de código
    # (OpenRouter, não Bedrock -- a correção rastreada é específica de
    # Bedrock) que ocasionalmente duplica o JSON dos argumentos de uma
    # tool call. Nosso guardrail (on_model_error_callback) já recupera
    # disso graciosamente -- mas numa sessão de má sorte, TODOS os 4
    # turnos podem ser afetados, e nenhuma tool call bem-sucedida
    # acontece. Isso não é sobre nosso código estar quebrado; é sobre
    # dar à conversa mais de uma chance de rodar sem essa instabilidade
    # específica, documentada e externa.
    last_state = None
    for attempt in range(3):
        state, _response_text = await _run_conversation(
            qualification_agent, conversation
        )
        if state.get(STATE_QUALIFICATION_STATUS) in {
            "qualified",
            "in_progress",
            "disqualified",
        }:
            return
        last_state = state

    flags = last_state.get(STATE_GUARDRAIL_FLAGS, [])
    pytest.fail(
        f"Falhou 3 vezes seguidas -- guardrail_flags do último attempt: {flags}"
    )


# --- SchedulingAgent (allowlist deveria bloquear sem qualificação prévia) ---


async def test_scheduling_blocks_booking_without_qualification():
    from app.agents.scheduling import scheduling_agent
    from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS

    state, _response_text = await _run_conversation(
        scheduling_agent,
        ["quero marcar uma reunião", "pode ser terça-feira às 10h"],
    )

    # Sessão nova = sem qualification_status setado = allowlist deveria
    # ter bloqueado book_meeting. Verificamos pelo flag de guardrail,
    # não pelo texto da resposta (que varia).
    flags = state.get(STATE_GUARDRAIL_FLAGS, [])
    assert any(
        "book_meeting" in f and "bloqueado" in f for f in flags
    ), f"Esperava um flag de bloqueio de book_meeting, achei: {flags}"


# --- Orchestrator (roteamento ponta a ponta) ---


async def test_orchestrator_routes_pricing_question_to_knowledge_agent():
    from app.agents import root_agent
    from app.agents.session.state_schema import STATE_LAST_RETRIEVED_CONTEXT

    state, _response_text = await _run_conversation(
        root_agent, ["quanto custa o plano de vocês?"]
    )

    # Confirma que o Orchestrator consultou o KnowledgeAgent -- pelo
    # state que ele escreve, não pelo texto exato da resposta (isso
    # continua válido depois que a Parte 4 trocar o stub por RAG real).
    assert STATE_LAST_RETRIEVED_CONTEXT in state


async def test_orchestrator_routes_objection_to_objection_agent():
    from app.agents import root_agent
    from app.agents.session.state_schema import STATE_OBJECTIONS_RAISED

    state, _response_text = await _run_conversation(
        root_agent, ["isso parece caro pra gente agora"]
    )

    assert STATE_OBJECTIONS_RAISED in state
