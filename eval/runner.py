"""
Roda um item do golden set contra o sistema real (agente ADK real, via
Runner + InMemorySessionService) e checa o critério ESTRUTURAL.

Mesmo padrão de `tests/test_golden_conversations.py::_run_conversation`
-- reimplementado aqui, não importado de lá: a direção de dependência
correta é `tests/` (roda via pytest, camada 3) e `eval/` (roda via
Dagster/LangFuse, camada 4) consumirem o mesmo PADRÃO de forma
independente, nenhum dos dois importando o outro.
"""

from __future__ import annotations

import uuid
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS

_APP_NAME = "sdr-bot-eval"


def _resolve_agent(target: str):
    """Mapeia o `target` de um GoldenSetItem pro objeto de agente real.
    Import tardio (dentro da função, não no topo do módulo) de
    propósito -- importar `app.agents` carrega ADK/LiteLLM/instrumentação
    do Phoenix como efeito colateral (ver app/agents/observability.py),
    e módulos deste pacote que só lidam com dados (golden_set.py,
    regression.py) precisam continuar importáveis sem isso."""
    if target == "root":
        from app.agents import root_agent

        return root_agent
    if target == "qualification":
        from app.agents.qualification import qualification_agent

        return qualification_agent
    if target == "knowledge":
        from app.agents.knowledge import knowledge_agent

        return knowledge_agent
    if target == "objection":
        from app.agents.objection import objection_agent

        return objection_agent
    if target == "scheduling":
        from app.agents.scheduling import scheduling_agent

        return scheduling_agent
    if target == "escalate":
        from app.agents.escalate import escalate_agent

        return escalate_agent
    raise ValueError(f"Alvo de agente desconhecido: {target!r}")


async def run_conversation(target: str, messages: list[str]) -> tuple[dict, str]:
    """Roda uma conversa multi-turno contra o agente real identificado
    por `target` e retorna (session.state final, texto da última
    resposta). Cada chamada usa uma sessão nova (uuid) -- sessões não
    devem vazar estado entre itens do golden set, mesma preocupação de
    tests/test_golden_conversations.py."""
    agent = _resolve_agent(target)
    session_service = InMemorySessionService()
    user_id = "eval-user"
    session_id = f"eval-{uuid.uuid4().hex[:8]}"

    await session_service.create_session(app_name=_APP_NAME, user_id=user_id, session_id=session_id)
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
    return dict(session.state), last_response_text


def check_structural(state: dict[str, Any], metadata: dict[str, Any]) -> tuple[bool, list[str]]:
    """Checa o critério ESTRUTURAL de um item do golden set contra o
    `session.state` final de uma conversa real -- o mesmo tipo de
    verificação que tests/test_golden_conversations.py já faz (chave
    de estado presente/ausente/com valor esperado, ou uma substring
    específica em guardrail_flags), só que orientado a dado (a partir
    do `metadata` de um GoldenSetItem serializado), não a asserts
    escritos à mão por teste.

    Função pura -- sem I/O, sem chamada de LLM -- testável com um
    `state` sintético (tests/test_eval_runner.py).

    Retorna (passou, lista_de_motivos) -- lista vazia quando passou,
    uma entrada legível por motivo de falha quando não.
    """
    reasons: list[str] = []

    for key in metadata.get("expected_state_keys", []):
        if key not in state:
            reasons.append(f"chave esperada ausente em session.state: {key!r}")

    for key, expected_value in metadata.get("expected_state_values", {}).items():
        actual_value = state.get(key)
        if actual_value != expected_value:
            reasons.append(
                f"session.state[{key!r}] esperava {expected_value!r}, veio {actual_value!r}"
            )

    for key in metadata.get("forbidden_state_keys", []):
        if key in state:
            reasons.append(f"chave proibida presente em session.state: {key!r}")

    flags = state.get(STATE_GUARDRAIL_FLAGS, []) or []
    flags_text = " | ".join(flags)
    for substring in metadata.get("expected_guardrail_flag_substrings", []):
        if substring not in flags_text:
            reasons.append(
                f"guardrail_flags não contém a substring esperada {substring!r} "
                f"(flags atuais: {flags!r})"
            )

    return (len(reasons) == 0, reasons)
