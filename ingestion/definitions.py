"""
Ponto de entrada do Dagster pra este projeto -- `dagster dev -f
ingestion/definitions.py` (ou via Makefile: `make ingest-dev`) abre a
UI local mostrando o grafo de assets, histórico de materialização, e
resultado dos asset_checks ao longo do tempo.

Rodar a ingestão sem UI (ex: num pipeline de CI/CD futuro):
    uv run dagster asset materialize -f ingestion/definitions.py --select '*'
"""

import dagster as dg

from ingestion.assets import (
    chroma_index,
    chunks,
    no_pii_leaked_into_index,
    raw_docs,
    retrieval_recall_at_k,
)

defs = dg.Definitions(
    assets=[raw_docs, chunks, chroma_index],
    asset_checks=[retrieval_recall_at_k, no_pii_leaked_into_index],
)
