"""
Regressão versionada: decide se a média de score de uma run do golden
set caiu o suficiente, em relação à baseline conhecida, pra falhar o
`asset_check` do pipeline (`eval/assets.py::eval_regression`).

Deliberadamente um arquivo local (`eval/baseline.json`, versionado em
git) em vez de só confiar na UI do LangFuse pra isso -- a UI mostra
tendência ao longo do tempo pra INSPEÇÃO humana, mas o objetivo aqui é
"a mudança de prompt/modelo deveria aparecer como uma métrica que sobe
ou desce" de forma AUTOMATIZADA, capaz de falhar um `asset_check`
sozinha. Um arquivo versionado também torna a baseline auditável via
`git blame`/PR review, igual qualquer outra mudança de comportamento
esperado do sistema.

A baseline só AVANÇA (nunca regride sozinha): `save_baseline` só é
chamado pelo pipeline quando a run atual não regrediu em relação à
baseline anterior (ver eval/assets.py). Isso evita o "sapo fervendo" --
várias pequenas quedas, cada uma dentro do threshold, empurrando a
baseline pra baixo aos poucos sem nenhuma delas nunca falhar o check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASELINE_PATH = Path(__file__).parent / "baseline.json"

# Em pontos, na escala 1-5 do JudgeScore.overall combinado com o
# structural_pass_rate (0-1, escalado pra 1-5 antes de comparar -- ver
# eval/assets.py). Escolhido como "uma queda perceptível, não ruído de
# amostragem de LLM" -- pequeno o bastante pra pegar regressão real,
# grande o bastante pra não disparar por variância normal de um modelo
# não-determinístico rodando poucas dezenas de itens.
DEFAULT_REGRESSION_THRESHOLD = 0.4


@dataclass
class RegressionResult:
    passed: bool
    current_avg: float
    baseline_avg: float | None
    delta: float | None
    message: str


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_baseline(
    average_score: float, extra: dict[str, Any] | None = None, path: Path = BASELINE_PATH
) -> None:
    data = {"average_score": average_score, **(extra or {})}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def compare_to_baseline(
    current_avg: float,
    baseline_avg: float | None,
    threshold: float = DEFAULT_REGRESSION_THRESHOLD,
) -> RegressionResult:
    """Compara a média da run atual contra a baseline conhecida.

    Sem baseline (primeira run, ou arquivo apagado/gitignored
    localmente): sempre passa -- não há nada pra comparar ainda, e essa
    run vira a baseline (decisão de QUANDO salvar fica no chamador, não
    aqui -- ver docstring do módulo sobre "baseline só avança").
    """
    if baseline_avg is None:
        return RegressionResult(
            passed=True,
            current_avg=current_avg,
            baseline_avg=None,
            delta=None,
            message="Sem baseline anterior -- esta run vira a baseline.",
        )

    delta = current_avg - baseline_avg
    passed = delta >= -threshold

    if passed and delta >= 0:
        message = (
            f"Sem regressão: {current_avg:.2f} vs. baseline "
            f"{baseline_avg:.2f} (delta {delta:+.2f})."
        )
    elif passed:
        message = (
            f"Queda dentro do threshold: {current_avg:.2f} vs. baseline "
            f"{baseline_avg:.2f} (delta {delta:+.2f}, threshold -{threshold:.2f})."
        )
    else:
        message = (
            f"REGRESSÃO: {current_avg:.2f} vs. baseline {baseline_avg:.2f} "
            f"(delta {delta:+.2f}, abaixo do threshold -{threshold:.2f})."
        )

    return RegressionResult(
        passed=passed,
        current_avg=current_avg,
        baseline_avg=baseline_avg,
        delta=delta,
        message=message,
    )
