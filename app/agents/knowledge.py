"""
Knowledge Agent — responde dúvidas sobre produto, preço e cases.

Nesta Parte 1 ele ainda NÃO tem RAG de verdade (isso é a Parte 4, via um
MCP server dedicado rodando sobre ChromaDB). Por enquanto ele responde só
com o que está na instruction, deixando claro ao lead quando não tem
certeza — importante para não estabelecer hábito de alucinar antes de
termos avaliação de faithfulness (Parte 6).
"""

from google.adk.agents import LlmAgent

from .config.models import get_model_for_role
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .guardrails.transfer import block_unauthorized_transfer
from .pii.masking import mask_pii
from .session.state_schema import STATE_LAST_RETRIEVED_CONTEXT

knowledge_agent = LlmAgent(
    name="KnowledgeAgent",
    model=get_model_for_role("knowledge"),
    description=(
        "Responde perguntas sobre o produto, funcionalidades, planos e "
        "preços. Use quando o lead pergunta 'o que vocês fazem', 'quanto "
        "custa', 'vocês integram com X', etc."
    ),
    instruction=(
        "Você responde perguntas sobre nosso produto SaaS de forma "
        "precisa e concisa. "
        "IMPORTANTE (temporário — Parte 1): você ainda não tem acesso à "
        "base de conhecimento real. Se não tiver certeza absoluta da "
        "resposta, diga explicitamente que vai confirmar com o time e "
        "NÃO invente números de preço ou funcionalidades. "
        "TODO Parte 4: este agente vai receber um MCPToolset apontando "
        "para um servidor MCP de retrieval (ChromaDB) — ver arquitetura "
        "no README."
    ),
    output_key=STATE_LAST_RETRIEVED_CONTEXT,
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail de transfer abaixo fica como defesa em profundidade.
    before_model_callback=[mask_pii, detect_prompt_injection],
    after_model_callback=[validate_output_policy, block_unauthorized_transfer],
)
