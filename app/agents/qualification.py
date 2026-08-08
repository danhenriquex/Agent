"""
Qualification Agent — conduz a descoberta inicial (framework BANT) para
decidir se o lead deveria avançar no funil comercial.

Nesta Parte 1, o agente só conversa e escreve um resumo em texto livre no
estado (via output_key). A Parte 6 (eval set) vai cobrar qualificação mais
estruturada e mensurável — ajustaremos o schema então.
"""

from google.adk.agents import LlmAgent

from ._guardrails import block_unauthorized_transfer
from .config.models import get_model_for_role
from .pii.masking import mask_pii
from .session.state_schema import STATE_QUALIFICATION_NOTES

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
        "Se o lead perguntar algo sobre o produto/preço que você não "
        "sabe, diga que vai verificar (o Orchestrator vai rotear para o "
        "agente certo)."
    ),
    output_key=STATE_QUALIFICATION_NOTES,
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail abaixo fica como defesa em profundidade, não a proteção
    # primária (ver docstring de _guardrails.py para o histórico do bug).
    before_model_callback=mask_pii,
    after_model_callback=block_unauthorized_transfer,
)
