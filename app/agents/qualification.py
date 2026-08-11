"""
Qualification Agent — conduz a descoberta inicial (framework BANT) para
decidir se o lead deveria avançar no funil comercial.

Parte 3: ganhou a tool set_qualification_status, que é a forma
estruturada e auditável de declarar "esse lead está qualificado" —
antes disso, só existia texto livre em qualification_notes, o que não
dava pra checagem confiável em outro lugar do sistema (ex: o
before_tool_callback de scheduling.py que agora depende desse status).

Padrão "booking-first" (o mesmo que Drift/Qualified/Chili Piper usam):
o objetivo não é completar um checklist BANT inteiro antes de sugerir
agendar -- é chegar a um convite pra marcar uma conversa o quanto antes,
usando qualificação como um filtro leve, não uma barreira. "in_progress"
é um status perfeitamente aceitável pra deixar pro SDR humano completar
na call, em vez de segurar o lead numa qualificação longa via chat.
"""

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext

from .config.models import get_model_for_role
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .guardrails.transfer import block_unauthorized_transfer
from .persona import PERSONA_INTRO
from .pii.masking import mask_pii
from .session.state_schema import STATE_QUALIFICATION_NOTES, STATE_QUALIFICATION_STATUS


def set_qualification_status(status: str, reasoning: str, tool_context: ToolContext) -> dict:
    """Registra o status de qualificação do lead com base na conversa até agora.

    Chame isso assim que tiver informação suficiente pra decidir —
    não precisa esperar cobrir todos os critérios BANT se já ficou
    claro que o lead é ou não um bom fit.

    Args:
        status: um de "qualified", "disqualified", "in_progress".
        reasoning: justificativa breve (1-2 frases) pra essa decisão,
            citando o que da conversa embasou isso.

    Returns:
        dict: confirmação do status registrado.
    """
    valid_statuses = {"qualified", "disqualified", "in_progress"}
    if status not in valid_statuses:
        return {
            "status": "error",
            "error_message": f"status inválido: {status!r}. Use um de {valid_statuses}.",
        }

    tool_context.state[STATE_QUALIFICATION_STATUS] = status
    return {"status": "success", "recorded_status": status, "reasoning": reasoning}


qualification_agent = LlmAgent(
    name="QualificationAgent",
    model=get_model_for_role("qualification"),
    description=(
        "Conduz a qualificação inicial do lead usando perguntas de "
        "descoberta (orçamento, autoridade de decisão, necessidade, "
        "prazo). Use quando o lead está no início da conversa ou quando "
        "ainda não sabemos se ele é um bom fit."
    ),
    instruction=(
        PERSONA_INTRO
        + "Você conduz a qualificação inicial de um lead B2B, mas de "
        "forma LEVE e RÁPIDA -- o objetivo é chegar a um convite pra "
        "agendar uma conversa o quanto antes, não completar um checklist "
        "inteiro antes de sugerir isso. Um SDR de verdade não interroga "
        "por várias perguntas seguidas antes de oferecer uma reunião.\n\n"
        "Faça no máximo 1-2 perguntas curtas e naturais sobre o desafio "
        "do lead (o que ele quer resolver) e, se render, o tamanho da "
        "empresa. Intercale com contexto útil sobre como ajudamos "
        "empresas parecidas -- não faça um interrogatório.\n\n"
        "Assim que tiver QUALQUER sinal razoável de fit -- não precisa "
        "ser orçamento, autoridade e prazo completos -- convide pra "
        "marcar uma conversa rápida com o time. 'in_progress' é um "
        "status perfeitamente aceitável: é normal deixar o resto da "
        "qualificação pra call em vez de segurar o lead no chat.\n\n"
        "Assim que tiver informação suficiente pra decidir, chame "
        "set_qualification_status com o status apropriado — isso é o "
        "que libera (ou não) o lead pra avançar pro agendamento. "
        "Se o lead perguntar algo sobre o produto/preço que você não "
        "sabe, diga que vai verificar (o Orchestrator vai rotear para o "
        "agente certo)."
    ),
    tools=[set_qualification_status],
    output_key=STATE_QUALIFICATION_NOTES,
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail de transfer abaixo fica como defesa em profundidade.
    before_model_callback=[mask_pii, detect_prompt_injection],
    after_model_callback=[validate_output_policy, block_unauthorized_transfer],
)
