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

from pathlib import Path

import chromadb
from fastmcp import FastMCP

CHROMA_DATA_DIR = Path(__file__).parent / "chroma_data"
COLLECTION_NAME = "helssing_knowledge_base"
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

mcp = FastMCP("helssing-knowledge-base")

_collection = None


def _get_collection():
    """Lazy singleton -- carregar o modelo de embedding é caro (mesmo
    motivo do singleton em app/agents/pii/engine.py na Parte 2), não
    queremos recarregar a cada chamada de tool."""
    global _collection
    if _collection is None:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

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
