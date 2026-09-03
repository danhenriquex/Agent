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
import threading
import time
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


def test_get_collection_singleton_init_is_thread_safe(monkeypatch):
    # Bug real encontrado rodando `make eval-run` (Parte 6):
    # eval/langfuse_client.py::run_experiment_sync roda vários itens do
    # golden set em paralelo (max_concurrency), e mais de um podia
    # chamar KnowledgeAgent ao mesmo tempo através do MESMO processo MCP
    # (McpToolset é um singleton por processo, ver
    # app/agents/knowledge.py). Sem trava, duas chamadas concorrentes
    # viam `_collection is None` ao mesmo tempo e as duas tentavam
    # construir um chromadb.PersistentClient sobre o mesmo diretório --
    # na prática isso quebrava com `AttributeError: 'RustBindingsAPI'
    # object has no attribute 'bindings'` seguido de `Could not connect
    # to tenant default_tenant`. Antes da Parte 6, nada neste projeto
    # chamava essa tool de forma concorrente, então essa corrida nunca
    # disparava.
    #
    # Este teste prova que o double-checked locking em _get_collection()
    # serializa a inicialização sob concorrência REAL de threads (não só
    # "no papel"): um `time.sleep` dentro do construtor falso alarga a
    # janela de corrida o bastante pra, sem a trava, múltiplas threads
    # quase certamente reconstruírem a coleção; com a trava, a segunda
    # checagem (dentro do lock) sempre vê `_collection` já setado pela
    # primeira thread que passou, e o construtor roda exatamente uma vez.
    monkeypatch.setattr(server_module, "_collection", None)

    build_count = 0
    build_lock = threading.Lock()

    class _FakeCollection:
        pass

    class _FakeClient:
        def get_collection(self, name, embedding_function):
            return _FakeCollection()

    def _fake_persistent_client(path):
        nonlocal build_count
        with build_lock:
            build_count += 1
        time.sleep(0.05)
        return _FakeClient()

    monkeypatch.setattr(server_module.chromadb, "PersistentClient", _fake_persistent_client)

    import chromadb.utils.embedding_functions as ef_module

    monkeypatch.setattr(
        ef_module, "SentenceTransformerEmbeddingFunction", lambda model_name: object()
    )

    results: list = []
    results_lock = threading.Lock()

    def _call():
        result = server_module._get_collection()
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert build_count == 1, f"PersistentClient foi construído {build_count} vez(es), deveria ser 1"
    assert len(results) == 8
    assert len({id(r) for r in results}) == 1, "threads concorrentes receberam coleções diferentes"
