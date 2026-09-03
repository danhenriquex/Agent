"""
Layer 2 de testes pra Parte 6: a lógica de comparação de regressão
(`eval/regression.py`) é aritmética pura sobre floats + leitura/escrita
de um JSON local -- não precisa de LLM nem de LangFuse pra testar.

Isso é o que valida, sem custar nenhuma chamada de API, que "baixou
demais vs. a baseline" realmente vira `passed=False` -- a mesma
verificação que o `asset_check` de regressão (eval/assets.py) usa de
verdade contra scores reais.
"""

import json

import pytest

from eval.regression import compare_to_baseline, load_baseline, save_baseline


def test_compare_to_baseline_passes_with_no_prior_baseline():
    result = compare_to_baseline(current_avg=3.5, baseline_avg=None)
    assert result.passed
    assert result.baseline_avg is None
    assert result.delta is None


def test_compare_to_baseline_passes_when_score_improves():
    result = compare_to_baseline(current_avg=4.2, baseline_avg=4.0)
    assert result.passed
    assert result.delta == pytest.approx(0.2)


def test_compare_to_baseline_passes_on_small_drop_within_threshold():
    result = compare_to_baseline(current_avg=3.9, baseline_avg=4.0, threshold=0.4)
    assert result.passed


def test_compare_to_baseline_fails_on_drop_beyond_threshold():
    # Isso é a regressão de propósito descrita no roadmap: um prompt
    # degradado o bastante pra derrubar a média em mais que o threshold
    # tem que reprovar o check, não só "parecer pior".
    result = compare_to_baseline(current_avg=3.0, baseline_avg=4.0, threshold=0.4)
    assert not result.passed
    assert "REGRESSÃO" in result.message


def test_compare_to_baseline_boundary_exactly_at_threshold_passes():
    # delta == -threshold é o limite -- passed usa >=, não >, então bate
    # exatamente na borda ainda deveria passar (não é uma regressão
    # "além" do threshold, é exatamente o threshold).
    result = compare_to_baseline(current_avg=3.6, baseline_avg=4.0, threshold=0.4)
    assert result.passed


def test_save_and_load_baseline_round_trip(tmp_path):
    path = tmp_path / "baseline.json"
    save_baseline(4.35, extra={"per_category": {"rag_faithfulness": 4.5}}, path=path)

    loaded = load_baseline(path=path)

    assert loaded["average_score"] == 4.35
    assert loaded["per_category"]["rag_faithfulness"] == 4.5


def test_load_baseline_returns_none_when_file_missing(tmp_path):
    path = tmp_path / "does_not_exist.json"
    assert load_baseline(path=path) is None


def test_save_baseline_writes_valid_json(tmp_path):
    path = tmp_path / "baseline.json"
    save_baseline(3.8, path=path)

    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["average_score"] == 3.8
