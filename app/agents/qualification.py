"""
Qualification Agent — conduz a descoberta inicial (framework BANT) para
decidir se o lead deveria avançar no funil comercial.

Parte 3: ganhou a tool set_qualification_status, que é a forma
estruturada e auditável de declarar "esse lead está qualificado" —
antes disso, só existia texto livre em qualification_notes, o que não
dava pra checagem confiável em outro lugar do sistema (ex: o
before_tool_callback de scheduling.py que agora depende desse status).
"""

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext

from .config.models import get_model_for_role
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .guardrails.transfer import block_unauthorized_transfer
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
        "Você é um SDR conduzindo a qualificação inicial de um lead B2B. "
        "Faça perguntas curtas e naturais, uma de cada vez, cobrindo ao "
        "longo da conversa: qual problema o lead quer resolver, se ele "
        "tem orçamento definido, se ele é o decisor ou influencia a "
        "decisão, e em que prazo pretende resolver isso. "
        "Não faça um interrogatório — intercale com contexto útil sobre "
        "como ajudamos empresas parecidas. "
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
