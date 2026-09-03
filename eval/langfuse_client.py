"""
Camada fina sobre o SDK `langfuse` (Python) -- concentra toda chamada
direta à API do LangFuse num lugar só, para que:

(a) o resto do pipeline de eval (golden_set, judge, regression, runner)
    não dependa da API exata do SDK e continue testável em camada 2 sem
    rede nem credenciais;
(b) side effects de rede (criar dataset, rodar o experimento, submeter
    scores) fiquem isolados, com uma checagem explícita
    (`is_configured`) em vez de deixar o SDK falhar com um erro
    genérico de conexão no meio do pipeline.

Reusa as MESMAS credenciais já usadas por `litellm_proxy/config.yaml`
(callback `langfuse_otel`, Parte 5) -- `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` -- em vez de introduzir uma
segunda forma de apontar pro mesmo LangFuse self-hospedado.

O dataset "sdr-bot-golden-set" é uma PROJEÇÃO de `eval/golden_set.py`:
`ensure_golden_dataset` faz upsert (via `id` de cada `GoldenSetItem`,
que o SDK usa como chave de atualização) toda vez que o pipeline roda,
então o dataset no LangFuse nunca fica divergente do que está
versionado em git por mais que um ciclo de execução.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Callable
from typing import Any

from .golden_set import GOLDEN_SET, GoldenSetItem

DATASET_NAME = "sdr-bot-golden-set"


def is_configured() -> bool:
    """True se as credenciais mínimas pro LangFuse self-hospedado
    (Parte 5) estão presentes no ambiente. Checar isso ANTES de chamar
    o SDK dá um erro acionável ("rode make langfuse-secrets") em vez de
    uma exceção de conexão genérica no meio do pipeline."""
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def get_client():
    """Retorna o cliente LangFuse (lê LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST
    do ambiente automaticamente, mesmo padrão de `get_model_for_role` em
    app/agents/config/models.py: a config vem do ambiente, não de um
    argumento espalhado pelo código)."""
    from langfuse import get_client as _get_client

    return _get_client()


def ensure_golden_dataset(
    items: list[GoldenSetItem] = GOLDEN_SET, dataset_name: str = DATASET_NAME
) -> str:
    """Cria (ou reusa) o dataset no LangFuse e faz upsert de cada item
    do golden set local nele. golden_set.py continua sendo a fonte de
    verdade versionada em git -- isso só publica uma cópia navegável na
    UI do LangFuse, nunca o contrário."""
    client = get_client()

    try:
        client.create_dataset(
            name=dataset_name,
            description=(
                "Golden eval set do SDR bot (Parte 6) -- fonte de verdade "
                "em eval/golden_set.py, este dataset é uma projeção dela."
            ),
        )
    except Exception:
        # A maioria das versões do SDK trata create_dataset como upsert,
        # mas algumas levantam em cima de um dataset já existente --
        # nesse caso seguimos em frente, o objetivo (dataset existir com
        # esse nome) já está satisfeito.
        pass

    for item in items:
        client.create_dataset_item(
            dataset_name=dataset_name,
            id=item.id,
            input=item.conversation,
            expected_output=item.qualitative_criteria,
            metadata=item.to_langfuse_metadata(),
        )

    return dataset_name


def get_golden_dataset(dataset_name: str = DATASET_NAME):
    return get_client().get_dataset(name=dataset_name)


def run_experiment_sync(
    dataset,
    *,
    name: str,
    task: Callable[..., Any],
    evaluators: list[Callable[..., Any]],
    description: str | None = None,
    max_concurrency: int = 3,
):
    """Roda `dataset.run_experiment(...)` e devolve o `ExperimentResult`.

    `task` aqui é async (precisa rodar uma conversa real via
    Runner.run_async, ver eval/runner.py). A documentação do SDK diz
    que funções de task/evaluator podem ser síncronas OU assíncronas,
    mas não deixa claro se `run_experiment` em si retorna um valor
    direto ou uma coroutine a depender da versão instalada -- em vez de
    travar nisso, tratamos os dois casos: se o retorno for aguardável,
    rodamos com asyncio.run; senão, usamos como está.

    max_concurrency baixo (3, não o default 50 do SDK) de propósito:
    cada item do golden set já dispara uma conversa multi-turno real
    contra o LiteLLM Proxy/OpenRouter (vários agentes especialistas) —
    50 em paralelo bateria em rate limit da OpenRouter bem antes de
    qualquer benefício de velocidade.
    """
    result = dataset.run_experiment(
        name=name,
        description=description,
        task=task,
        evaluators=evaluators,
        max_concurrency=max_concurrency,
    )
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return result
