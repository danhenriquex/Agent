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
acessível (testado manualmente: falha de export só fica logada, nunca
propaga) -- seguro chamar isso incondicionalmente, inclusive em
testes/CI onde o Phoenix não está rodando.

batch=True (BatchSpanProcessor) É INTENCIONAL mesmo sabendo que, em
Cloud Run, ele nunca chega a exportar nada -- ver INCIDENTE abaixo
antes de tentar "consertar" trocando pra batch=False de novo.

Estado conhecido: tracing pra Phoenix **não funciona em produção**
(Cloud Run) hoje, só localmente (`make phoenix-up`). Root cause do
lado do batch=True: Cloud Run só aloca CPU pro container ENQUANTO ele
processa uma requisição (sdr-bot-api não roda com
--no-cpu-throttling, de propósito, pelo custo recorrente que isso
significaria) -- a thread de background que o BatchSpanProcessor usa
pra fazer flush a cada 5s nunca chega a ser escalonada entre
requisições, então spans só se acumulam em memória e nunca são
exportados (confirmado: zero requisições em /v1/traces nos logs do
Cloud Run do Phoenix, mesmo com /chat retornando 200 normalmente).

INCIDENTE (não repetir): trocar pra batch=False (SimpleSpanProcessor,
export síncrono, inline no request) foi tentado como correção e
DERRUBOU sdr-bot-api em produção -- os exports síncronos começaram a
falhar por timeout ("Failed to export span batch due to timeout, max
retries or shutdown"), cada timeout bloqueando o request inteiro, até
esgotar as 3 instâncias (maxScale) só com requisições penduradas;
`/health` chegou a não responder mais. Revertido via rollback de
tráfego pro revision anterior. Causa exata do timeout do export em si
NÃO foi identificada (um teste direto de fora do Cloud Run pro
endpoint /v1/traces do Phoenix respondeu rápido) -- possivelmente
específico da chamada saindo de DENTRO do container do sdr-bot-api,
não investigado a fundo por causa do risco de repetir o incidente.

Até essa investigação ser retomada (ou a infra de tracing ser trocada
por algo que não dependa de Cloud Run agendar uma thread de
background, ex: LangFuse com storage externo, ou --no-cpu-throttling
assumindo o custo), fica batch=True: sem traces em produção, mas sem
risco de derrubar o /chat de verdade.

DESABILITADO EM PRODUÇÃO (PHOENIX_ENABLED não setado lá, de propósito):
mesmo com batch=True, confirmado em produção que o processo tenta
repetidamente uma conexão gRPC direta em <host>:4317 (a porta gRPC do
Phoenix, NUNCA configurada -- PHOENIX_COLLECTOR_ENDPOINT aponta pro
endpoint HTTP em :6006/v1/traces) além do export HTTP configurado,
ambos falhando/re-tentando durante o request inteiro -- consumindo
tempo real de CPU/GIL disputado com o processamento da própria
conversa (rodando lado a lado com "Failed to export span batch due to
timeout" nos mesmos segundos de uma resposta que devia ser rápida).
Causa exata da tentativa gRPC ainda não identificada (não é nenhuma
env var nossa -- só PHOENIX_COLLECTOR_ENDPOINT está setado, e ele já
aponta pro endpoint HTTP certo; suspeita de algum default interno do
openinference-instrumentation-google-adk ou do próprio phoenix.otel,
não investigado a fundo). Local (`make phoenix-up`) continua
funcionando via PHOENIX_ENABLED=true no .env -- só produção fica
desligada até isso ser root-causado com calma, não sob pressão de um
chatbot fora do ar.
"""

import os

from opentelemetry import trace

from . import config  # noqa: F401 -- garante que .env já foi carregado

_PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "sdr-bot")

if os.environ.get("PHOENIX_ENABLED", "").lower() in ("1", "true", "yes"):
    from phoenix.otel import register

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
