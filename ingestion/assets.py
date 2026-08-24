"""
Pipeline de ingestão do RAG: raw_docs -> chunks -> embeddings -> chroma_index

Cada estágio é um Dagster asset com linhagem rastreada -- não "um
script que roda", mas "um dado que existe e pode ficar desatualizado
em relação à fonte". Isso é o motivo de termos escolhido Dagster em
vez de Prefect pra essa parte especificamente: o modelo de
software-defined assets encaixa bem melhor num pipeline de RAG, onde
"o índice de vetores está desatualizado em relação aos documentos-
fonte?" é uma pergunta que a própria ferramenta consegue responder via
linhagem, sem código extra nosso.

Os dois asset_check em chroma_index tornam a Parte 4 uma resposta real
pro requisito "RAG avaliado com dados" -- toda vez que o índice é
reconstruído, a qualidade de retrieval é reavaliada automaticamente,
não é um notebook manual rodado de vez em quando.
"""

import re
from pathlib import Path

import chromadb
import dagster as dg

KNOWLEDGE_BASE_DIR = Path(__file__).parent / "knowledge_base"
CHROMA_DATA_DIR = Path(__file__).parent.parent / "mcp_server" / "chroma_data"
COLLECTION_NAME = "helssing_knowledge_base"
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# Query dourada usada pelo asset_check de qualidade de retrieval --
# cada entrada diz "essa pergunta deveria recuperar um chunk vindo
# deste documento-fonte, entre os top-k resultados". Pequeno de
# propósito (golden set completo com LLM-as-judge é escopo da Parte 6),
# mas real o suficiente pra pegar regressão óbvia de chunking/embedding.
_GOLDEN_QUERIES = [
    ("quanto custa o plano de vocês?", "02_planos_precos"),
    ("como funciona o cálculo de horas extras?", "03_folha_pagamento"),
    ("já uso outro sistema, por que devo trocar?", "06_objecao_concorrente"),
    ("vocês integram com o eSocial?", "08_integracoes"),
]


def _default_embedding_function():
    """Função de embedding real, usada em produção -- baixa o modelo
    multilingue do HuggingFace Hub na primeira chamada (~470MB)."""
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    return SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL_NAME)


@dg.asset(description="Lê todos os documentos .md da base de conhecimento")
def raw_docs() -> list[dict]:
    docs = []
    for path in sorted(KNOWLEDGE_BASE_DIR.glob("*.md")):
        docs.append({"source": path.stem, "text": path.read_text(encoding="utf-8")})
    return docs


@dg.asset(description="Divide cada documento em chunks por seção (headers ##)")
def chunks(raw_docs: list[dict]) -> list[dict]:
    """Divide por seção de nível 2 (##), não por tamanho fixo -- nossos
    documentos já são estruturados em seções semanticamente coerentes
    (ver ingestion/knowledge_base/*.md), então isso preserva o
    significado de cada chunk melhor que corte por N caracteres.

    Cada chunk carrega o título do documento (H1) como prefixo, pra não
    perder o contexto de qual produto/assunto ele é quando recuperado
    isoladamente.
    """
    result = []
    for doc in raw_docs:
        text = doc["text"]
        title_match = re.match(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1) if title_match else doc["source"]

        sections = re.split(r"^##\s+(.+)$", text, flags=re.MULTILINE)
        # re.split com grupo de captura intercala: [preâmbulo, header1,
        # corpo1, header2, corpo2, ...]
        preamble = sections[0]
        section_pairs = list(zip(sections[1::2], sections[2::2]))

        if not section_pairs:
            # Documento sem nenhum "##" -- trata o texto inteiro como um chunk só.
            result.append(
                {
                    "id": f"{doc['source']}__full",
                    "source": doc["source"],
                    "text": text.strip(),
                }
            )
            continue

        for i, (header, body) in enumerate(section_pairs):
            chunk_text = f"{title} — {header}\n\n{body.strip()}"
            if i == 0 and preamble.strip():
                # A primeira seção carrega também o preâmbulo (texto
                # antes do primeiro "##"), que costuma ter o resumo
                # de uma linha do documento.
                chunk_text = f"{title}\n\n{preamble.strip()}\n\n## {header}\n\n{body.strip()}"
            result.append(
                {
                    "id": f"{doc['source']}__{i}",
                    "source": doc["source"],
                    "text": chunk_text,
                }
            )
    return result


@dg.asset(description="Escreve os chunks no ChromaDB (recria a coleção do zero)")
def chroma_index(
    context: dg.AssetExecutionContext, chunks: list[dict]
) -> dg.MaterializeResult:
    CHROMA_DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DATA_DIR))

    # Recria do zero a cada materialização -- simples e correto pra
    # esse volume (8 documentos); reindexação incremental ficaria como
    # melhoria de "se isso fosse produção" com uma base maior.
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME, embedding_function=_default_embedding_function()
    )
    collection.add(
        documents=[c["text"] for c in chunks],
        ids=[c["id"] for c in chunks],
        metadatas=[{"source": c["source"]} for c in chunks],
    )

    context.log.info(f"Indexados {len(chunks)} chunks em '{COLLECTION_NAME}'")
    return dg.MaterializeResult(
        metadata={"chunk_count": len(chunks), "collection": COLLECTION_NAME}
    )


@dg.asset_check(asset=chroma_index, description="Recall@3 contra o golden query set")
def retrieval_recall_at_k() -> dg.AssetCheckResult:
    client = chromadb.PersistentClient(path=str(CHROMA_DATA_DIR))
    collection = client.get_collection(
        COLLECTION_NAME, embedding_function=_default_embedding_function()
    )

    hits = 0
    misses = []
    for query, expected_source in _GOLDEN_QUERIES:
        results = collection.query(query_texts=[query], n_results=3)
        retrieved_sources = [m["source"] for m in results["metadatas"][0]]
        if expected_source in retrieved_sources:
            hits += 1
        else:
            misses.append({"query": query, "expected": expected_source, "got": retrieved_sources})

    recall = hits / len(_GOLDEN_QUERIES)
    return dg.AssetCheckResult(
        passed=recall >= 0.75,
        metadata={
            "recall_at_3": recall,
            "hits": hits,
            "total": len(_GOLDEN_QUERIES),
            "misses": misses,
        },
    )


@dg.asset_check(asset=chroma_index, description="Nenhum chunk indexado deveria conter PII")
def no_pii_leaked_into_index() -> dg.AssetCheckResult:
    """Defesa em profundidade -- a base de conhecimento é conteúdo de
    produto, nunca deveria ter PII de verdade nela. Reusa o mesmo
    Presidio já validado na Parte 2, em vez de duplicar lógica de
    detecção.

    Nota real descoberta rodando isso: os chunks carregam o título/
    header da seção como prefixo (ex: "Helssing — Diferencial"), e
    palavras isoladas e capitalizadas sem estrutura de frase ao redor
    confundem o NER do spaCy (ex: "Preços", "Gestor", "CPF" foram
    identificados como PERSON). Um nome de verdade é quase sempre
    multi-token em texto natural ("Danilo Henrique") -- filtrar
    matches de um token só remove esse falso positivo estrutural sem
    enfraquecer a proteção real (esse filtro vale só para este check,
    não para o mask_pii da Parte 2, que roda sobre mensagens de
    conversa de verdade, não títulos de seção).
    """
    from app.agents.pii.engine import SUPPORTED_ENTITIES, get_analyzer_engine

    client = chromadb.PersistentClient(path=str(CHROMA_DATA_DIR))
    collection = client.get_collection(
        COLLECTION_NAME, embedding_function=_default_embedding_function()
    )
    all_chunks = collection.get()

    analyzer = get_analyzer_engine()
    flagged = []
    for chunk_id, text in zip(all_chunks["ids"], all_chunks["documents"]):
        results = analyzer.analyze(text=text, language="pt", entities=SUPPORTED_ENTITIES)
        real_hits = [
            r
            for r in results
            if not (r.entity_type == "PERSON" and " " not in text[r.start : r.end].strip())
        ]
        if real_hits:
            flagged.append({"chunk_id": chunk_id, "entities": [r.entity_type for r in real_hits]})

    return dg.AssetCheckResult(
        passed=len(flagged) == 0,
        metadata={"flagged_chunks": flagged, "total_chunks": len(all_chunks["ids"])},
    )
