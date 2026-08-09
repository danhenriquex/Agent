"""
Objection Handling Agent — lida com objeções comuns (preço, concorrente,
"preciso falar com meu time", timing).

Parte 3: a regra "nunca ofereça desconto" era só uma instrução de
prompt (facilmente contornável por prompt injection) — agora também é
aplicada via validate_output_policy, que checa a resposta do modelo
DE VERDADE, não só confia que ele vai seguir a instrução.
"""

from google.adk.agents import LlmAgent

from .config.models import get_model_for_role
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .guardrails.transfer import block_unauthorized_transfer
from .pii.masking import mask_pii
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
        "com um account executive para discutir condições comerciais."
    ),
    output_key=STATE_OBJECTIONS_RAISED,
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail de transfer abaixo fica como defesa em profundidade.
    before_model_callback=[mask_pii, detect_prompt_injection],
    after_model_callback=[validate_output_policy, block_unauthorized_transfer],
)
