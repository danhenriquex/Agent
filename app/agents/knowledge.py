"""
Knowledge Agent — responde dúvidas sobre produto, preço e cases.

Nesta Parte 1 ele ainda NÃO tem RAG de verdade (isso é a Parte 4, via um
MCP server dedicado rodando sobre ChromaDB). Por enquanto ele responde só
com o que está na instruction, deixando claro ao lead quando não tem
certeza — importante para não estabelecer hábito de alucinar antes de
termos avaliação de faithfulness (Parte 6).
"""

from google.adk.agents import LlmAgent

from app.agents._guardrails import block_unauthorized_transfer
from app.config.models import get_model_for_role
from app.session.state_schema import STATE_LAST_RETRIEVED_CONTEXT

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
    # guardrail abaixo fica como defesa em profundidade, não a proteção
    # primária (ver docstring de _guardrails.py para o histórico do bug).
    after_model_callback=block_unauthorized_transfer,
)
