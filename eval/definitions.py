"""
Ponto de entrada do Dagster pra Parte 6 -- `dagster dev -f
eval/definitions.py` (ou via Makefile: `make eval-dev`) abre a UI local
mostrando o pipeline de avaliação e o resultado do asset_check de
regressão ao longo do tempo. Mesmo padrão de ingestion/definitions.py.

Rodar sem UI (usado por `make eval-run`):
    uv run dagster asset materialize -f eval/definitions.py --select '*'
"""

import dagster as dg

from eval.assets import eval_regression, eval_run, golden_set_items

defs = dg.Definitions(
    assets=[golden_set_items, eval_run],
    asset_checks=[eval_regression],
)
