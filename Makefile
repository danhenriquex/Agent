SHELL := /bin/bash
.DEFAULT_GOAL := help

PROXY_COMPOSE := litellm_proxy/docker-compose.yml
AGENTS_DIR := app/agents
PROXY_READY_URL := http://localhost:4000/health/readiness
PROXY_READY_TIMEOUT := 30

# Mesmo backend de sessão usado por app/session_service.py -- setar aqui
# também faz `adk web` gravar no MESMO arquivo/banco que `make api` lê,
# então conversas testadas na UI do adk web ficam visíveis via
# GET /handoff/{user_id}/{session_id}/history no Swagger. `adk web` não
# tem flag pra escolher app_name (usa sempre o nome do diretório de
# agentes, "agents" nesse projeto) -- por isso SDR_APP_NAME no .env
# precisa estar setado como "agents" pra bater com o que a API espera.
SESSION_DB_URL ?= sqlite+aiosqlite:///./sdr_bot_sessions.db

.PHONY: help sync proxy-up proxy-down proxy-restart proxy-logs proxy-status \
        web cli api test test-pii test-guardrails test-live lint clean \
        ingest-dev langfuse-secrets langfuse-up langfuse-down phoenix-up \
        eval-run eval-dev

help:
	@echo "Comandos disponíveis:"
	@echo ""
	@echo "  make sync          - uv sync (instala/atualiza dependências)"
	@echo ""
	@echo "  make web           - sobe o proxy (se preciso) e abre a UI do adk web (porta 8000)"
	@echo "  make cli           - sobe o proxy (se preciso) e roda o bot via CLI"
	@echo "  make api           - sobe o proxy (se preciso) e roda a API FastAPI em localhost:8001"
	@echo ""
	@echo "  make proxy-up      - sobe o LiteLLM Proxy e espera ele responder de verdade"
	@echo "  make proxy-down    - derruba o LiteLLM Proxy"
	@echo "  make proxy-restart - derruba e sobe de novo (útil após editar .env)"
	@echo "  make proxy-logs    - segue os logs do proxy"
	@echo "  make proxy-status  - mostra se o container está de pé"
	@echo ""
	@echo "  make test          - roda a suite de testes completa"
	@echo "  make test-pii      - roda só os testes de PII (mais rápido pra iterar)"
	@echo "  make test-guardrails - roda só os testes de guardrails (Parte 3)"
	@echo "  make test-live     - conversas douradas contra o LLM real (custa"
	@echo "                       API, sobe o proxy sozinho) — NÃO entra em 'make test'"
	@echo "  make ingest-dev    - abre a UI do Dagster pra rodar a ingestão do RAG"
	@echo ""
	@echo "  make eval-run      - roda o golden eval set completo (LLM-judge,"
	@echo "                       custa API, precisa do proxy e do LangFuse de pé)"
	@echo "  make eval-dev      - abre a UI do Dagster pra rodar o eval set (Parte 6)"
	@echo ""
	@echo "  make phoenix-up      - sobe o Phoenix local (tracing de agentes/RAG)"
	@echo "  make langfuse-up     - sobe o LangFuse self-hospedado (custo/tokens)"
	@echo "  make langfuse-down   - derruba o LangFuse"
	@echo "  make langfuse-secrets - gera os ~10 segredos do LangFuse (só imprime)"
	@echo "  make lint          - roda o ruff"
	@echo "  make clean         - remove __pycache__/.pytest_cache/.ruff_cache"

sync:
	uv sync

# Sobe o proxy e espera de verdade ele responder antes de liberar o
# próximo comando -- isso existe especificamente porque "docker compose
# up -d" retorna assim que o CONTAINER inicia, não quando o processo
# LiteLLM lá dentro termina de registrar os modelos e está pronto pra
# aceitar conexão. Sem esperar isso, curl/a aplicação podem chegar
# primeiro e receber "empty reply from server" -- foi exatamente o que
# aconteceu depurando isso manualmente antes deste Makefile existir.
#
# A checagem de LANGFUSE_OTEL_HOST/LANGFUSE_HOST abaixo existe porque o
# callback langfuse_otel (litellm_proxy/config.yaml) sem NENHUM dos dois
# setados não falha nem loga erro -- ele silenciosamente manda os spans
# pro endpoint US do Langfuse CLOUD (fallback hardcoded no próprio
# litellm). Ou seja: sem essa var, "funciona" sem erro nenhum, só que
# os dados de custo/tokens vazam pra fora em vez de ir pro LangFuse
# self-hospedado -- pior tipo de bug, silencioso. Descoberto lendo o
# código-fonte do callback (litellm/integrations/langfuse/langfuse_otel.py),
# não documentado no README da lib.
proxy-up:
	@python3 -c "\
from pathlib import Path; \
import re, sys; \
env_path = Path('.env'); \
env_text = env_path.read_text() if env_path.exists() else ''; \
values = dict(re.findall(r'^([A-Z_]+)=(.*)\$$', env_text, re.MULTILINE)); \
has_host = bool(values.get('LANGFUSE_OTEL_HOST', '').strip() or values.get('LANGFUSE_HOST', '').strip()); \
has_keys = bool(values.get('LANGFUSE_PUBLIC_KEY', '').strip() and values.get('LANGFUSE_SECRET_KEY', '').strip()); \
sys.exit(0) if (has_host and has_keys) or not (has_host or has_keys) else (\
    print('ERRO: LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY estão setados mas'), \
    print('LANGFUSE_OTEL_HOST (nem LANGFUSE_HOST) não está -- o callback'), \
    print('langfuse_otel vai mandar os spans pro Langfuse CLOUD em vez do'), \
    print('seu self-hosted, SEM erro nenhum. Adicione ao .env:'), \
    print('  LANGFUSE_OTEL_HOST=http://host.docker.internal:3000'), \
    sys.exit(1))"
	@docker compose -f $(PROXY_COMPOSE) up -d
	@echo -n "Esperando o proxy ficar pronto"
	@for i in $$(seq 1 $(PROXY_READY_TIMEOUT)); do \
		if curl -sf $(PROXY_READY_URL) > /dev/null 2>&1; then \
			echo " OK"; \
			exit 0; \
		fi; \
		echo -n "."; \
		sleep 1; \
	done; \
	echo ""; \
	echo "ERRO: proxy não respondeu após $(PROXY_READY_TIMEOUT)s."; \
	echo "Rode 'make proxy-logs' para ver o que aconteceu."; \
	exit 1

proxy-down:
	docker compose -f $(PROXY_COMPOSE) down

proxy-restart: proxy-down proxy-up

proxy-logs:
	docker compose -f $(PROXY_COMPOSE) logs -f litellm-proxy

proxy-status:
	docker compose -f $(PROXY_COMPOSE) ps

web: proxy-up
	uv run adk web --session_service_uri="$(SESSION_DB_URL)" $(AGENTS_DIR)

cli: proxy-up
	uv run python -m app.main

api: proxy-up
	uv run uvicorn app.api:app --reload --port 8001

test:
	uv run pytest -v

test-pii:
	uv run pytest tests/test_pii_masking.py -v

test-guardrails:
	uv run pytest tests/test_prompt_injection.py tests/test_output_policy.py \
		tests/test_action_allowlist.py tests/test_guardrails.py -v

# Testes ao vivo (Layer 3): custam chamadas reais de API, por isso não
# entram em "make test". Sobe o proxy (se preciso) antes de rodar.
test-live: proxy-up
	RUN_LIVE_TESTS=1 uv run pytest tests/test_golden_conversations.py -v

ingest-dev:
	uv run dagster dev -f ingestion/definitions.py

# Parte 6: golden eval set + LLM-as-judge. Custa chamadas reais de LLM
# (uma conversa por item do golden set, mais uma chamada ao judge-model
# por item) -- por isso, como test-live, nunca roda em `make test`/CI.
# Precisa do LangFuse (make langfuse-up) já de pé além do proxy -- os
# scores são submetidos lá, não guardados só localmente.
eval-run: proxy-up
	@python3 -c "\
from pathlib import Path; \
import re, sys; \
env_path = Path('.env'); \
env_text = env_path.read_text() if env_path.exists() else ''; \
values = dict(re.findall(r'^([A-Z_]+)=(.*)\$$', env_text, re.MULTILINE)); \
missing = [k for k in ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY') if not values.get(k, '').strip()]; \
sys.exit(0) if not missing else (\
    print('ERRO: eval-run submete scores ao LangFuse -- faltam no .env:'), \
    [print(f'  - {m}') for m in missing], \
    print('Rode make langfuse-secrets, cole no .env, e make langfuse-up antes.'), \
    sys.exit(1))"
	uv run dagster asset materialize -f eval/definitions.py --select '*'

eval-dev:
	uv run dagster dev -f eval/definitions.py

# Parte 5: gera os ~10 segredos do LangFuse de uma vez -- só IMPRIME,
# nunca escreve no .env sozinho (mesmo espírito de PII_HASH_SALT:
# você gera, você cola). Rodar de novo gera valores NOVOS -- não é
# idempotente de propósito, já que cada segredo devia ser único.
langfuse-secrets:
	@echo "Cole estas linhas no seu .env (substituindo os valores vazios):"
	@echo ""
	@python3 -c "\
import secrets; \
names = ['LANGFUSE_SALT', 'LANGFUSE_ENCRYPTION_KEY', 'LANGFUSE_NEXTAUTH_SECRET', \
'LANGFUSE_POSTGRES_PASSWORD', 'LANGFUSE_CLICKHOUSE_PASSWORD', 'LANGFUSE_REDIS_AUTH', \
'LANGFUSE_MINIO_ROOT_PASSWORD', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', \
'LANGFUSE_INIT_USER_PASSWORD']; \
[print(f'{name}={secrets.token_hex(32)}') for name in names]"

# --env-file .env é OBRIGATÓRIO aqui, não redundante com o env_file: ../.env
# de dentro do compose -- descoberto rodando de verdade: o Docker Compose
# resolve interpolação de ${VAR} no PRÓPRIO YAML (os "environment:"/"command:"
# usados por postgres/redis/minio) usando um .env procurado no diretório do
# arquivo compose (langfuse/.env, que não existe), não no diretório onde o
# comando é executado. O `env_file: ../.env` funciona (injeta variáveis DENTRO
# do container em runtime), mas isso é uma etapa totalmente separada da
# interpolação -- sem --env-file .env, toda ${LANGFUSE_*} vira string vazia
# na hora de montar o compose, e foi exatamente isso que causava o Redis
# crashar com "wrong number of arguments" (bug do requirepass) e o Postgres
# recusar subir por "superuser password is not specified", mesmo com os
# segredos certos já colados no .env.
langfuse-up:
	@echo "Verificando segredos do LangFuse no .env..."
	@python3 -c "\
from pathlib import Path; \
import re, sys; \
env_path = Path('.env'); \
env_text = env_path.read_text() if env_path.exists() else ''; \
values = dict(re.findall(r'^([A-Z_]+)=(.*)\$$', env_text, re.MULTILINE)); \
required = ['LANGFUSE_SALT', 'LANGFUSE_ENCRYPTION_KEY', 'LANGFUSE_NEXTAUTH_SECRET', \
    'LANGFUSE_POSTGRES_PASSWORD', 'LANGFUSE_CLICKHOUSE_PASSWORD', 'LANGFUSE_REDIS_AUTH', \
    'LANGFUSE_MINIO_ROOT_PASSWORD', 'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', \
    'LANGFUSE_INIT_USER_PASSWORD']; \
missing = [k for k in required if not values.get(k, '').strip()]; \
sys.exit(0) if not missing else (\
    print('ERRO: os seguintes segredos estão vazios/ausentes no .env:'), \
    [print(f'  - {m}') for m in missing], \
    print(), \
    print('Rode: make langfuse-secrets'), \
    print('E cole TODOS os valores gerados no seu .env antes de tentar de novo.'), \
    sys.exit(1))"
	docker compose -f langfuse/docker-compose.yml --env-file .env up -d

langfuse-down:
	docker compose -f langfuse/docker-compose.yml --env-file .env down

phoenix-up:
	uv run python -m phoenix.server.main serve

lint:
	uv run ruff check app tests

clean:
	find . -name "__pycache__" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".ruff_cache" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
