"""
Scheduling Agent — tarefa estruturada: verifica disponibilidade e
"agenda" a reunião (mock nesta Parte 1, sem calendário real).

Modelo: barato/rápido (ver app/config/models.py) porque a tarefa é
majoritariamente extração estruturada, não raciocínio aberto.

Este foi o primeiro agente com tools reais no projeto — propositalmente,
para validar que tool-calling funciona ponta a ponta através do LiteLLM
Proxy + OpenRouter antes de adicionarmos tools mais sensíveis (RAG, MCP)
nas próximas partes.

Parte 3: book_meeting agora é protegido por enforce_action_allowlist
(before_tool_callback) — só executa de verdade se
session.state[STATE_QUALIFICATION_STATUS] == "qualified". Isso é
allowlist de AÇÃO (o que o sistema pode EXECUTAR), diferente dos
guardrails de conteúdo (o que o sistema pode DIZER).
"""

from google.adk.agents import LlmAgent

from .config.models import get_model_for_role
from .guardrails.action_allowlist import enforce_action_allowlist
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .guardrails.transfer import block_unauthorized_transfer
from .pii.masking import mask_pii
from .session.state_schema import STATE_MEETING_SLOT

_MOCK_SLOTS = ["terça-feira às 10h", "quarta-feira às 15h", "quinta-feira às 11h"]


def check_availability() -> dict:
    """Retorna os horários disponíveis para reunião com um account executive.

    Returns:
        dict: status e lista de horários disponíveis.
    """
    return {"status": "success", "available_slots": _MOCK_SLOTS}


def book_meeting(slot: str) -> dict:
    """Confirma o agendamento de uma reunião em um horário específico.

    Args:
        slot: um dos horários retornados por check_availability
            (ex: "terça-feira às 10h").

    Returns:
        dict: status da confirmação.
    """
    if slot not in _MOCK_SLOTS:
        return {
            "status": "error",
            "error_message": (
                f"Horário '{slot}' não está disponível. "
                "Use check_availability primeiro."
            ),
        }

    # A checagem de qualificação acontece ANTES desta função sequer
    # rodar -- ver enforce_action_allowlist (before_tool_callback).
    return {"status": "success", "confirmed_slot": slot}


scheduling_agent = LlmAgent(
    name="SchedulingAgent",
    model=get_model_for_role("scheduling"),
    description=(
        "Agenda reuniões de apresentação com um account executive. Use "
        "quando o lead já demonstrou interesse em marcar uma conversa ou "
        "avançar para uma próxima etapa comercial."
    ),
    instruction=(
        "Você ajuda o lead a marcar uma reunião. Primeiro chame "
        "check_availability para ver os horários livres, apresente as "
        "opções de forma natural, e depois chame book_meeting com o "
        "horário escolhido. Seja objetivo — esse não é o momento de "
        "reabrir a qualificação ou discutir preço. Se book_meeting "
        "retornar status 'blocked', explique ao lead de forma natural "
        "que precisa completar o perfil antes, usando o link fornecido "
        "no error_message."
    ),
    tools=[check_availability, book_meeting],
    output_key=STATE_MEETING_SLOT,
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail de transfer abaixo fica como defesa em profundidade.
    before_model_callback=[mask_pii, detect_prompt_injection],
    after_model_callback=[validate_output_policy, block_unauthorized_transfer],
    before_tool_callback=enforce_action_allowlist,
)
