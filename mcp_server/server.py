"""
Servidor MCP que expõe retrieval sobre a base de conhecimento da
Helssing. Roda como processo SEPARADO do app principal (igual o
LiteLLM Proxy) -- o ADK conecta nele via subprocess/stdio
(StdioConnectionParams em app/agents/knowledge.py), não como import
Python. É por isso que este arquivo pode ficar fora de app/agents/ sem
esbarrar na restrição de import-root isolado do `adk web` que já nos
mordeu duas vezes nas Partes 1 e 2.

Lê o MESMO diretório ChromaDB que ingestion/assets.py escreve
(mcp_server/chroma_data/) -- rode a ingestão pelo menos uma vez
(`make ingest-dev`) antes de usar este servidor, senão a coleção não
existe ainda.
"""

import threading
from pathlib import Path

import chromadb
from fastmcp import FastMCP

CHROMA_DATA_DIR = Path(__file__).parent / "chroma_data"
COLLECTION_NAME = "helssing_knowledge_base"
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

mcp = FastMCP("helssing-knowledge-base")

_collection = None
# Bug real encontrado rodando `make eval-run` (Parte 6): o pipeline de
# eval é o primeiro código deste projeto a chamar KnowledgeAgent a
# partir de VÁRIAS conversas ao mesmo tempo (via
# eval/langfuse_client.py::run_experiment_sync, max_concurrency=3) --
# todas elas passam pelo mesmo processo/conexão MCP (McpToolset é um
# singleton por processo, ver app/agents/knowledge.py). Sem trava, duas
# chamadas concorrentes podiam ver `_collection is None` ao mesmo tempo
# e as duas tentar construir um chromadb.PersistentClient sobre o MESMO
# diretório simultaneamente -- na prática isso se manifestou como
# `AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'`
# seguido de `Could not connect to tenant default_tenant`, uma
# inicialização vendo o binding Rust do client concorrente ainda pela
# metade. Antes da Parte 6, nada neste projeto chamava essa tool de
# forma concorrente, então essa condição de corrida nunca disparava.
_collection_lock = threading.Lock()


def _get_collection():
    """Lazy singleton -- carregar o modelo de embedding é caro (mesmo
    motivo do singleton em app/agents/pii/engine.py na Parte 2), não
    queremos recarregar a cada chamada de tool.

    Double-checked locking: a checagem rápida sem lock mantém o caminho
    comum (coleção já carregada) sem contenção nenhuma; a trava só entra
    em jogo na janela estreita da primeira inicialização, exatamente
    onde a corrida acontecia.
    """
    global _collection
    if _collection is None:
        with _collection_lock:
            if _collection is None:
                from chromadb.utils.embedding_functions import (
                    SentenceTransformerEmbeddingFunction,
                )

                client = chromadb.PersistentClient(path=str(CHROMA_DATA_DIR))
                ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL_NAME)
                _collection = client.get_collection(COLLECTION_NAME, embedding_function=ef)
    return _collection


@mcp.tool()
def retrieve_product_docs(query: str, top_k: int = 3) -> list[dict]:
    """Busca trechos relevantes da base de conhecimento da Helssing
    (produto, funcionalidades, planos, objeções comuns, integrações)
    por similaridade semântica.

    Args:
        query: a pergunta ou tópico do lead, em português.
        top_k: quantos trechos retornar (padrão 3).

    Returns:
        list[dict]: cada item tem 'text' (o trecho) e 'source'
        (documento de origem, útil pra saber a procedência da
        informação).
    """
    collection = _get_collection()
    results = collection.query(query_texts=[query], n_results=top_k)

    return [
        {"text": doc, "source": meta["source"]}
        for doc, meta in zip(results["documents"][0], results["metadatas"][0])
    ]


@mcp.tool()
def get_pricing_info() -> str:
    """Retorna o documento de planos e preços completo, direto — sem
    depender de busca semântica. Perguntas de preço são comuns e
    previsíveis o suficiente pra merecer um caminho determinístico em
    vez de confiar só em similarity search (que pode, em tese, não
    trazer o chunk certo pra queries de preço fora do padrão).

    Returns:
        str: o conteúdo completo do documento de planos e preços.
    """
    collection = _get_collection()
    result = collection.get(where={"source": "02_planos_precos"})
    return "\n\n".join(result["documents"])


if __name__ == "__main__":
    mcp.run()
