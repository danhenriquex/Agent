"""
Objection Handling Agent — lida com objeções comuns (preço, concorrente,
"preciso falar com meu time", timing).
"""

from google.adk.agents import LlmAgent

from ._guardrails import block_unauthorized_transfer
from .config.models import get_model_for_role
from .session.state_schema import STATE_OBJECTIONS_RAISED

objection_agent = LlmAgent(
    name="ObjectionHandlingAgent",
    model=get_model_for_role("objection"),
    description=(
        "Lida com objeções e resistências do lead (preço alto, já usa "
        "concorrente, precisa de aprovação interna, não é prioridade "
        "agora). Use quando o lead expressar hesitação ou recusa."
    ),
    instruction=(
        "Você lida com objeções de forma empática, sem ser insistente. "
        "Reconheça a objeção antes de responder a ela. Nunca ofereça "
        "desconto, condição especial ou prazo que não foi explicitamente "
        "autorizado — se o lead pedir desconto, diga que pode conectar "
        "com um account executive para discutir condições comerciais. "
        "TODO Parte 3: essa regra de 'nunca ofereça desconto' precisa "
        "virar um guardrail verificável (after_model_callback), não só "
        "uma instrução no prompt — instrução sozinha não é garantia "
        "contra prompt injection."
    ),
    output_key=STATE_OBJECTIONS_RAISED,
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail abaixo fica como defesa em profundidade, não a proteção
    # primária (ver docstring de _guardrails.py para o histórico do bug).
    after_model_callback=block_unauthorized_transfer,
)
