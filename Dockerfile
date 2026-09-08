# syntax=docker/dockerfile:1
#
# Dockerfile — sdr-bot-api
#
# Build em 2 estágios seguindo o padrão oficial do uv
# (https://docs.astral.sh/uv/guides/integration/docker/):
#   1. builder: resolve e instala dependências com uv, usando cache de
#      camada separado para dependências vs. código (rebuild rápido
#      quando só o código muda) — e instala SÓ o grupo de produção
#      (--no-dev), então pytest/ruff nunca entram na imagem final.
#   2. runtime: imagem limpa, só com o .venv já resolvido + o código da
#      aplicação. Nada de uv, pip, ou cache de build sobra aqui.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Camada de dependências, cacheada separadamente do código: só reinstala
# se pyproject.toml/uv.lock mudarem, não a cada commit no código.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY pyproject.toml uv.lock ./
COPY app/ ./app/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Materializa o índice RAG (ChromaDB) DURANTE o build -- decisão já
# documentada em ARCHITECTURE.md, nunca antes implementada de fato: o
# Dockerfile só copiava app/, então mcp_server/server.py nem existia no
# container, e KnowledgeAgent quebrava com "[Errno 2] No such file or
# directory" na primeira pergunta de RAG em produção. Baixa o modelo de
# embedding (~470MB) e roda a ingestão real aqui -- trade-off aceito:
# rebuild necessário a cada mudança na base de conhecimento
# (ingestion/knowledge_base/*.md), em troca de não precisar de GCS +
# sync em runtime nem de recursos Terraform novos.
#
# PII_HASH_SALT: qualquer valor serve aqui -- não é dado real de
# produção, só precisa existir pra importar app.agents.pii.engine (o
# asset_check no_pii_leaked_into_index) sem levantar erro de config
# ausente.
#
# PYTHONPATH=/app: sem isso, o asset_check no_pii_leaked_into_index
# falha com "ModuleNotFoundError: No module named 'app'" -- descoberto
# rodando o build de verdade. O executor multiprocess do Dagster
# lança um subprocess NOVO por step (confirmado nos logs: "Launching
# subprocess for..."), e esse subprocess específico (o único que faz
# `from app.agents.pii.engine import ...`, os outros steps não tocam
# em app/) não herda /app no sys.path do jeito que uma invocação
# direta herdaria.
COPY ingestion/ ./ingestion/
COPY mcp_server/server.py ./mcp_server/server.py

RUN --mount=type=cache,target=/root/.cache/uv \
    PII_HASH_SALT=build-time-only-not-a-real-secret-000000000000 \
    PYTHONPATH=/app \
    uv run dagster asset materialize -f ingestion/definitions.py --select '*'

FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app ./app
COPY --from=builder /app/mcp_server ./mcp_server

# Sem isso, o modelo de embedding (baixado no builder pra materializar
# o índice acima) era baixado de NOVO da HuggingFace Hub, pela rede, a
# cada instância nova em produção -- runtime não herdava esse cache de
# forma nenhuma antes (só /app/.venv, /app/app, /app/mcp_server eram
# copiados). Descoberto em produção de verdade: "MCP tool execution
# failed with McpError: Timed out while waiting for response to
# ClientRequest. Waited 15.0 seconds." na primeira pergunta de RAG de
# cada instância fria -- o download pela rede (~470MB, sem HF_TOKEN,
# sujeito a rate limit mais baixo) estourava o timeout da tool call
# quase sempre. Com o cache local já presente, o carregamento é do
# disco (visto no build: "Loading weights: 100%" em ~1-2s), não da
# rede.
COPY --from=builder /root/.cache/huggingface /root/.cache/huggingface

# HF_HUB_OFFLINE=1: sem isso, o cache acima só evita o RE-DOWNLOAD dos
# pesos -- sentence-transformers/huggingface_hub ainda faz um HEAD
# de rede a cada carregamento pra checar se há versão mais nova, MESMO
# com o arquivo local presente. Descoberto em produção de verdade:
# esse HEAD tomou 429 da HF ("Rate limited. Waiting 182.0s before
# retry"), e esse bloqueio sozinho (não o download) já estourava o
# timeout de 15s da tool call MCP, deixando tool_calls órfãos e
# derrubando a conversa inteira. Setar aqui, não no builder -- o
# builder PRECISA de rede pra buscar o modelo a primeira vez.
ENV HF_HUB_OFFLINE=1

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT}"]
