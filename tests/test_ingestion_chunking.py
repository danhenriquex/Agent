"""
Layer 2 de testes pra Parte 4: chunks() é lógica pura (regex +
manipulação de string), não precisa de ChromaDB nem de modelo de
embedding pra testar. raw_docs() só lê arquivos do disco -- também sem
dependência externa.
"""

from ingestion.assets import chunks, raw_docs


def test_chunks_splits_by_section_headers():
    docs = [
        {
            "source": "doc_teste",
            "text": (
                "# Título do Documento\n\nIntro.\n\n"
                "## Seção A\n\nConteúdo A.\n\n"
                "## Seção B\n\nConteúdo B."
            ),
        }
    ]

    result = chunks(docs)

    assert len(result) == 2
    assert result[0]["id"] == "doc_teste__0"
    assert result[1]["id"] == "doc_teste__1"
    assert all(c["source"] == "doc_teste" for c in result)


def test_chunks_first_chunk_includes_title_and_preamble():
    docs = [
        {
            "source": "doc_teste",
            "text": "# Meu Produto\n\nUm resumo de uma linha.\n\n## Primeira Seção\n\nTexto aqui.",
        }
    ]

    result = chunks(docs)

    assert "Meu Produto" in result[0]["text"]
    assert "Um resumo de uma linha" in result[0]["text"]
    assert "Primeira Seção" in result[0]["text"]


def test_chunks_handles_document_without_any_section_headers():
    docs = [{"source": "doc_sem_secoes", "text": "# Título\n\nTexto corrido sem nenhum ##."}]

    result = chunks(docs)

    assert len(result) == 1
    assert result[0]["id"] == "doc_sem_secoes__full"


def test_chunks_generates_unique_ids_across_multiple_docs():
    docs = [
        {"source": "doc_a", "text": "# A\n\n## Um\n\nX\n\n## Dois\n\nY"},
        {"source": "doc_b", "text": "# B\n\n## Um\n\nZ"},
    ]

    result = chunks(docs)
    ids = [c["id"] for c in result]

    assert len(ids) == len(set(ids))


def test_raw_docs_reads_all_knowledge_base_files():
    docs = raw_docs()

    assert len(docs) == 8
    sources = {d["source"] for d in docs}
    assert "01_visao_geral" in sources
    assert "02_planos_precos" in sources
    for d in docs:
        assert d["text"].startswith("#")


def test_real_knowledge_base_chunks_without_error():
    # Integração leve: confirma que a base de conhecimento REAL (não
    # sintética) processa sem erro -- pega regressão se alguém editar
    # um .md de um jeito que quebre o parsing de seção.
    docs = raw_docs()
    result = chunks(docs)

    assert len(result) > len(docs)
    assert all(c["id"] and c["source"] and c["text"] for c in result)
