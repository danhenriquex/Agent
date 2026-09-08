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

import logging
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

_logger = logging.getLogger(__name__)


async def warmup_mcp_connection() -> None:
    """Pré-aquece a conexão MCP -- spawna o subprocess (mcp_server/
    server.py), faz o handshake stdio, e lista as tools -- ANTES do
    primeiro request real chegar.

    Sem isso, a primeira pergunta de RAG em cada instância nova paga o
    boot do subprocess (~20s+, medido em produção real: do início do
    request até o banner do FastMCP aparecer no log) DENTRO do próprio
    request do usuário. Descoberto rodando de verdade: essa lentidão
    disparava retries internos do ADK/LiteLLM na chamada da tool, e
    CADA retry deixava um tool_call órfão (sem tool_result
    correspondente) permanentemente gravado na sessão -- uma sessão
    chegou a acumular 25+ tool_calls órfãos numa ÚNICA requisição,
    corrompendo a conversa pro resto do histórico (toda mensagem
    seguinte reenvia o mesmo histórico quebrado pro LLM). Chamado no
    lifespan de startup do FastAPI (app/api.py).

    Não aquece o modelo de embedding em si (isso só carrega no
    PRIMEIRO uso real de uma tool, dentro do subprocess -- ver
    mcp_server/server.py::_get_collection) -- só o boot do processo e a
    conexão MCP, que é a fatia dominante do tempo medido. Chamar uma
    tool de verdade aqui pra aquecer o embedding também exigiria
    replicar a machinery interna do McpToolset pra invocar uma tool
    fora de um agent run -- fora de escopo por ora; o ganho principal
    já vem do boot do subprocess.

    Seguro falhar aqui -- não impede o startup do app, só significa
    que o warmup não rolou e a conexão volta a acontecer lazy no
    primeiro uso real (comportamento de antes desta mudança).
    """
    try:
        await _knowledge_base_mcp.get_tools()
    except Exception:
        _logger.warning("Falha ao pré-aquecer conexão MCP", exc_info=True)


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
