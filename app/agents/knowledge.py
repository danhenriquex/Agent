"""
Knowledge Agent — responde dúvidas sobre produto, preço e cases.

Parte 4: ganhou RAG de verdade via McpToolset, conectado a um servidor
MCP dedicado (mcp_server/server.py) que roda sobre ChromaDB. O servidor
é um PROCESSO SEPARADO (igual o LiteLLM Proxy) -- o ADK conecta nele via
subprocess/stdio, não como import Python. É por isso que mcp_server/ e
ingestion/ podem ficar fora de app/agents/ sem esbarrar na restrição de
import-root isolado do `adk web` (Partes 1 e 2).

Pré-requisito pra isso funcionar: a base de conhecimento precisa ter
sido indexada pelo menos uma vez (`make ingest-dev`), senão o servidor
MCP não encontra a coleção no ChromaDB.
"""

import sys
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

from .config.models import get_model_for_role
from .guardrails.model_error_recovery import recover_from_duplicated_tool_call_json
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .guardrails.transfer import block_unauthorized_transfer
from .persona import PERSONA_INTRO
from .pii.masking import mask_pii
from .session.state_schema import STATE_LAST_RETRIEVED_CONTEXT

# app/agents/knowledge.py -> app/agents -> app -> raiz do projeto.
# Calculado em runtime (não hardcoded) pra funcionar independente de
# onde o projeto foi clonado.
_PROJECT_ROOT = Path(__file__).parent.parent.parent

_knowledge_base_mcp = McpToolset(
    connection_params=StdioConnectionParams(
        # sys.executable, não "uv"/"uv run python": o processo atual
        # (seja `uv run uvicorn ...` local ou o CMD direto do container
        # em produção) já roda sob o interpretador certo -- reusar
        # sys.executable funciona nos dois ambientes sem exigir que `uv`
        # esteja instalado em produção (o runtime stage do Dockerfile
        # nunca teve `uv`, só o builder stage tem).
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["mcp_server/server.py"],
            cwd=str(_PROJECT_ROOT),
        ),
        timeout=15.0,
    ),
)

knowledge_agent = LlmAgent(
    name="KnowledgeAgent",
    model=get_model_for_role("knowledge"),
    description=(
        "Responde perguntas sobre o produto, funcionalidades, planos e "
        "preços. Use quando o lead pergunta 'o que vocês fazem', 'quanto "
        "custa', 'vocês integram com X', etc."
    ),
    instruction=(
        PERSONA_INTRO
        + "Você responde perguntas sobre nosso produto SaaS de forma "
        "precisa e concisa, usando as ferramentas de busca disponíveis "
        "-- NUNCA responda sobre preço, funcionalidade ou integração "
        "de memória sem antes consultar retrieve_product_docs ou "
        "get_pricing_info.\n\n"
        "Para perguntas de preço/planos, use get_pricing_info (retorna "
        "o documento completo, mais confiável que busca semântica pra "
        "esse tipo de pergunta). Para tudo mais sobre produto, "
        "funcionalidades, objeções ou integrações, use "
        "retrieve_product_docs com a pergunta do lead.\n\n"
        "Se o resultado da busca não cobrir o que foi perguntado, diga "
        "explicitamente que vai confirmar com o time -- NÃO invente "
        "números de preço ou funcionalidades que não vieram da busca."
    ),
    tools=[_knowledge_base_mcp],
    output_key=STATE_LAST_RETRIEVED_CONTEXT,
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail de transfer abaixo fica como defesa em profundidade.
    before_model_callback=[mask_pii, detect_prompt_injection],
    after_model_callback=[validate_output_policy, block_unauthorized_transfer],
    on_model_error_callback=recover_from_duplicated_tool_call_json,
)
