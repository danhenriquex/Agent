"""
Layer 2 de testes pra Parte 4: as tools do servidor MCP, testadas
diretamente (sem protocolo MCP de verdade, sem subprocess) usando um
embedder falso e determinístico -- não precisam do modelo real
(huggingface.co) pra validar a LÓGICA das tools.

get_pricing_info() usa filtro de METADADO (collection.get(where=...)),
não busca semântica -- funciona identicamente com qualquer embedder,
então o teste dela é uma prova real de correção, não só estrutural.

retrieve_product_docs() usa busca semântica de verdade -- aqui só
testamos ESTRUTURA do retorno (formato, top_k respeitado), não
relevância. Relevância semântica já foi validada rodando o pipeline
real (ver README > RAG, recall@3 = 1.0 no golden query set).
"""

import hashlib
import uuid

import numpy as np
import pytest
from chromadb import EmbeddingFunction, EphemeralClient

import mcp_server.server as server_module


class _FakeEmbeddingFunction(EmbeddingFunction):
    def __init__(self):
        pass

    def __call__(self, input):
        return [
            (
                np.frombuffer(hashlib.sha256(t.encode()).digest(), dtype=np.uint8)[:16]
                / 255.0
            ).tolist()
            for t in input
        ]

    def name(self) -> str:
        return "fake-hash-embedder"


@pytest.fixture
def fake_collection(monkeypatch):
    client = EphemeralClient()
    # Nome único por teste -- EphemeralClient compartilha estado dentro
    # do mesmo processo de teste, então um nome fixo colide entre testes
    # rodando na mesma sessão do pytest.
    collection = client.create_collection(
        name=f"test_collection_{uuid.uuid4().hex[:8]}",
        embedding_function=_FakeEmbeddingFunction(),
    )
    collection.add(
        documents=[
            "Helssing — Planos e Preços\n\nPlano Starter custa R$12 por funcionário/mês.",
            "Helssing — Folha de Pagamento\n\nCalcula horas extras automaticamente.",
            "Helssing — Objeção: É caro\n\nReconheça a preocupação com custo.",
        ],
        ids=["02_planos_precos__0", "03_folha_pagamento__0", "07_objecao_preco__0"],
        metadatas=[
            {"source": "02_planos_precos"},
            {"source": "03_folha_pagamento"},
            {"source": "07_objecao_preco"},
        ],
    )
    monkeypatch.setattr(server_module, "_collection", collection)
    yield collection
    monkeypatch.setattr(server_module, "_collection", None)


def test_retrieve_product_docs_returns_correct_structure(fake_collection):
    results = server_module.retrieve_product_docs("qualquer pergunta", top_k=2)

    assert len(results) == 2
    for r in results:
        assert set(r.keys()) == {"text", "source"}


def test_retrieve_product_docs_respects_custom_top_k(fake_collection):
    results = server_module.retrieve_product_docs("qualquer pergunta", top_k=1)

    assert len(results) == 1


def test_get_pricing_info_returns_only_pricing_document(fake_collection):
    result = server_module.get_pricing_info()

    assert "R$12" in result
    assert "Folha de Pagamento" not in result
    assert "Objeção" not in result


def test_collection_singleton_is_reused_not_rebuilt(fake_collection, monkeypatch):
    # _get_collection() não deveria tentar reconstruir a coleção (e
    # baixar o modelo de embedding) se _collection já está setado --
    # é isso que torna os testes acima possíveis sem rede. Prova isso
    # fazendo SentenceTransformerEmbeddingFunction explodir se for
    # instanciada -- é essa a chamada que baixaria o modelo real.
    import chromadb.utils.embedding_functions as ef_module

    def _boom(*args, **kwargs):
        raise AssertionError(
            "não deveria reconstruir a coleção -- _collection já está setado"
        )

    monkeypatch.setattr(ef_module, "SentenceTransformerEmbeddingFunction", _boom)

    server_module.retrieve_product_docs("teste", top_k=1)  # não deveria levantar
