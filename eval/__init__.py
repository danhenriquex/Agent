"""
Parte 6: golden eval set + LLM-as-judge + regressão versionada.

Ver README.md, seção "Eval set + LLM-as-judge (Parte 6)", para a
arquitetura completa. Resumo: `golden_set.py` é a fonte de verdade
versionada em git (não o dataset no LangFuse, que é uma PROJEÇÃO dela);
`judge.py` pontua respostas reais contra o critério qualitativo de cada
item; `regression.py` decide se uma run regrediu em relação à baseline
conhecida; `assets.py`/`definitions.py` amarram tudo num pipeline
Dagster, no mesmo padrão de `ingestion/`.
"""
