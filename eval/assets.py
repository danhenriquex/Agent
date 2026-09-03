"""
Pipeline de avaliação (Parte 6): golden_set_items -> eval_run -> asset_check
de regressão. Mesmo padrão de software-defined assets de ingestion/assets.py
(Parte 4) -- cada estágio é um dado que existe e pode ficar desatualizado em
relação à fonte, não "um script que roda".

`eval_run` é o único estágio que custa chamadas reais de LLM (uma
conversa completa por item do golden set, mais uma chamada ao
judge-model por item) -- por isso este pipeline, como test-live, nunca
roda em `make test`/CI por padrão, só via `make eval-run`.
"""

from statistics import mean
from typing import Any

import dagster as dg

from .golden_set import GOLDEN_SET
from .langfuse_client import (
    DATASET_NAME,
    ensure_golden_dataset,
    get_golden_dataset,
    run_experiment_sync,
)
from .regression import compare_to_baseline, load_baseline, save_baseline
from .runner import check_structural, run_conversation

# Cap deliberado: uma resposta que falha o critério ESTRUTURAL (ex:
# book_meeting não deveria ter sido confirmado, ou o agente escalou
# quando não devia) não é "boa" mesmo que o tom esteja ótimo -- sem
# isso, um item poderia tirar nota alta do juiz (que só vê texto,
# nunca session.state) enquanto viola uma regra de negócio real.
_STRUCTURAL_FAILURE_SCORE_CAP = 2.0


async def _task(*, item, **_kwargs) -> dict[str, Any]:
    """Task do experimento LangFuse: roda a conversa do item contra o
    agente real identificado em item.metadata['target'].

    `async def` de propósito, e chamando `await run_conversation(...)`
    diretamente (sem `asyncio.run`) -- descoberto rodando `make eval-run`
    de verdade: `dataset.run_experiment(...)` já executa a task DENTRO
    do próprio event loop do SDK (é assim que ele consegue rodar vários
    itens em paralelo via `max_concurrency`), então uma task síncrona
    que chama `asyncio.run()` internamente colide com esse loop já
    rodando ("asyncio.run() cannot be called from a running event
    loop"). O SDK aceita task functions síncronas OU assíncronas (ver
    eval/langfuse_client.py::run_experiment_sync) -- deixar a nossa
    async e deixar o SDK dar o await evita esse aninhamento por
    completo, em vez de tentar gerenciar o loop manualmente aqui.
    """
    state, response_text = await run_conversation(item.metadata["target"], item.input)
    return {"response_text": response_text, "state": state}


def _structural_evaluator(*, output, metadata, **_kwargs):
    from langfuse import Evaluation

    passed, reasons = check_structural(output["state"], metadata)
    return Evaluation(
        name="structural_pass",
        value=1.0 if passed else 0.0,
        data_type="BOOLEAN",
        comment="; ".join(reasons) if reasons else "Critério estrutural atendido.",
    )


def _judge_evaluator(*, input, output, metadata, **_kwargs):
    from langfuse import Evaluation

    from .judge import judge_response

    score = judge_response(
        conversation=input,
        response_text=output["response_text"],
        qualitative_criteria=metadata["qualitative_criteria"],
        reference_context=metadata.get("reference_context"),
    )

    evaluations = [
        Evaluation(name="judge_criteria_adherence", value=score.criteria_adherence),
        Evaluation(name="judge_tone_and_persona", value=score.tone_and_persona),
        Evaluation(name="judge_overall", value=score.overall, comment=score.justification),
    ]
    if score.faithfulness is not None:
        evaluations.append(Evaluation(name="judge_faithfulness", value=score.faithfulness))
    return evaluations


@dg.asset(description="Sincroniza eval/golden_set.py como Dataset no LangFuse (sdr-bot-golden-set)")
def golden_set_items() -> dg.MaterializeResult:
    ensure_golden_dataset(GOLDEN_SET)
    return dg.MaterializeResult(
        metadata={
            "dataset_name": DATASET_NAME,
            "item_count": len(GOLDEN_SET),
            "categories": sorted({item.category for item in GOLDEN_SET}),
        }
    )


@dg.asset(
    description="Roda cada item do golden set contra o sistema real e pontua com o judge-model",
    deps=[golden_set_items],
)
def eval_run(context: dg.AssetExecutionContext) -> list[dict[str, Any]]:
    dataset = get_golden_dataset(DATASET_NAME)

    result = run_experiment_sync(
        dataset,
        name=f"eval-run-{context.run_id[:8]}",
        description="Golden set completo (Parte 6) -- ver eval/golden_set.py.",
        task=_task,
        evaluators=[_structural_evaluator, _judge_evaluator],
    )

    # Descoberto rodando `make eval-run` de verdade: quando a task de um
    # item levanta uma exceção (erro de rede, bug do LiteLLM com JSON de
    # tool call duplicado -- já documentado em model_error_recovery.py
    # pra conversas normais, mas o pipeline de eval não tem esse mesmo
    # guardrail), o item ainda aparece em result.item_results, só que
    # sem nenhuma Evaluation associada (os evaluators nunca rodam sobre
    # um output que não existe). Tratar isso como "média sobre o que
    # sobrou" mascararia justamente o tipo de falha que este pipeline
    # existe pra pegar -- por isso um item sem os dois scores esperados
    # é coletado como falha e derruba a run inteira, em vez de só sumir
    # da média silenciosamente.
    rows: list[dict[str, Any]] = []
    failed_ids: list[str] = []
    for item_result in result.item_results:
        golden_set_id = item_result.item.metadata["golden_set_id"]
        evals_by_name = {e.name: e for e in item_result.evaluations}

        if "structural_pass" not in evals_by_name or "judge_overall" not in evals_by_name:
            failed_ids.append(golden_set_id)
            continue

        structural_passed = evals_by_name["structural_pass"].value == 1.0
        judge_overall = float(evals_by_name["judge_overall"].value)
        combined_score = (
            judge_overall
            if structural_passed
            else min(judge_overall, _STRUCTURAL_FAILURE_SCORE_CAP)
        )
        rows.append(
            {
                "golden_set_id": golden_set_id,
                "category": item_result.item.metadata["category"],
                "structural_passed": structural_passed,
                "structural_reasons": evals_by_name["structural_pass"].comment,
                "judge_overall": judge_overall,
                "combined_score": combined_score,
            }
        )

    if failed_ids or len(rows) != len(GOLDEN_SET):
        raise RuntimeError(
            f"eval_run: {len(failed_ids) or len(GOLDEN_SET) - len(rows)} item(ns) do "
            "golden set não produziram score (falha na task -- ver logs acima pra "
            f"a exceção original). Itens sem score: {failed_ids or 'desconhecidos'}. "
            "Rodar o asset_check de regressão sobre uma média parcial daria uma "
            "falsa sensação de confiança -- rode `make eval-run` de novo depois de "
            "investigar a causa (rede, rate limit da OpenRouter, ou o bug de JSON "
            "duplicado do LiteLLM já documentado em "
            "app/agents/guardrails/model_error_recovery.py)."
        )

    avg = mean(r["combined_score"] for r in rows)
    context.log.info(f"eval_run: {len(rows)} itens, média combinada = {avg:.2f}")
    return rows


@dg.asset_check(
    asset=eval_run,
    description="Regressão: média combinada não pode cair vs. baseline salva",
)
def eval_regression(eval_run: list[dict[str, Any]]) -> dg.AssetCheckResult:
    current_avg = mean(row["combined_score"] for row in eval_run)

    baseline = load_baseline()
    baseline_avg = baseline["average_score"] if baseline else None

    outcome = compare_to_baseline(current_avg, baseline_avg)

    per_category: dict[str, float] = {}
    categories = {row["category"] for row in eval_run}
    for category in categories:
        scores = [row["combined_score"] for row in eval_run if row["category"] == category]
        per_category[category] = mean(scores)

    # A baseline só AVANÇA -- só salvamos quando esta run não regrediu,
    # pra baseline nunca deslizar pra baixo através de várias pequenas
    # quedas, cada uma dentro do threshold (ver eval/regression.py).
    if outcome.passed and (baseline_avg is None or current_avg >= baseline_avg):
        save_baseline(current_avg, {"per_category": per_category})

    return dg.AssetCheckResult(
        passed=outcome.passed,
        metadata={
            "current_average": current_avg,
            "baseline_average": baseline_avg if baseline_avg is not None else -1.0,
            "delta": outcome.delta if outcome.delta is not None else 0.0,
            "message": outcome.message,
            "per_category": per_category,
            "structural_failures": [
                row["golden_set_id"] for row in eval_run if not row["structural_passed"]
            ],
        },
    )
