"""
Configura tracing OpenTelemetry -> Phoenix como efeito colateral da
primeira importação de app.agents (ver app/agents/__init__.py) — roda
igual pra CLI, API, testes, e `adk web`, já que todos passam por esse
import.

Fica dentro de app/agents/ (não em app/observability.py) pela mesma
razão de pii/ e guardrails/ nas Partes 2 e 3 -- `adk web` isola
app/agents/ como raiz de import, sem visibilidade de módulos irmãos
fora dela.

register() nunca levanta exceção mesmo se o Phoenix não estiver
acessível (testado manualmente: BatchSpanProcessor exporta em
background, falha de export só fica logada, nunca propaga) -- seguro
chamar isso incondicionalmente, inclusive em testes/CI onde o Phoenix
não está rodando.
"""

import os

from opentelemetry import trace
from phoenix.otel import register

from . import config  # noqa: F401 -- garante que .env já foi carregado

_PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "sdr-bot")

register(
    project_name=_PROJECT_NAME,
    batch=True,
    auto_instrument=True,
    verbose=False,
)


def annotate_current_span(event_name: str, **attributes: str) -> None:
    """Anota o span ATIVO do OpenTelemetry (criado automaticamente pelo
    openinference-instrumentation-google-adk ao redor de cada
    agent_run) com um evento de guardrail -- assim os traces do
    Phoenix mostram não só QUE um agente rodou, mas ONDE
    especificamente um guardrail disparou dentro dele (PII mascarado,
    injection bloqueado, ação negada, erro recuperado).

    Usa add_event() (não set_attribute()) de propósito -- um disparo
    de guardrail é uma OCORRÊNCIA pontual dentro do span, não uma
    propriedade persistente dele; eventos aparecem na timeline do
    trace no Phoenix, exatamente o formato certo pra isso.

    Seguro chamar mesmo sem tracing configurado ou sem span ativo --
    trace.get_current_span() sempre retorna um objeto (NoOp se não
    houver span/tracer ativo), nunca levanta exceção. Verificado
    manualmente: contexto de span propaga corretamente através de uma
    chamada de função aninhada comum, exatamente o padrão dos nossos
    callbacks de guardrail.
    """
    span = trace.get_current_span()
    span.add_event(event_name, attributes=attributes)
