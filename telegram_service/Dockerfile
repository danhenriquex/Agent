# syntax=docker/dockerfile:1
#
# Dockerfile — telegram_service
#
# Mesmo padrão de 2 estágios do Dockerfile raiz (sdr-bot-api), com deps
# próprias e mínimas (fastapi/uvicorn/httpx) -- nenhuma das dependências
# pesadas do projeto principal (torch/chromadb/dagster) entra aqui, o
# que mantém a imagem pequena e o build rápido.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY pyproject.toml uv.lock ./
COPY main.py ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/main.py ./main.py

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8200
EXPOSE 8200

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
