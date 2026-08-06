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

FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app ./app

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT}"]
