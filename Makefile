SHELL := /bin/bash
.DEFAULT_GOAL := help

PROXY_COMPOSE := litellm_proxy/docker-compose.yml
AGENTS_DIR := app/agents
PROXY_READY_URL := http://localhost:4000/health/readiness
PROXY_READY_TIMEOUT := 30

.PHONY: help sync proxy-up proxy-down proxy-restart proxy-logs proxy-status \
        web cli api test test-pii lint clean

help:
	@echo "Comandos disponíveis:"
	@echo ""
	@echo "  make sync          - uv sync (instala/atualiza dependências)"
	@echo ""
	@echo "  make web           - sobe o proxy (se preciso) e abre a UI do adk web"
	@echo "  make cli           - sobe o proxy (se preciso) e roda o bot via CLI"
	@echo "  make api           - sobe o proxy (se preciso) e roda a API FastAPI (--reload)"
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
proxy-up:
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
	uv run adk web $(AGENTS_DIR)

cli: proxy-up
	uv run python -m app.main

api: proxy-up
	uv run uvicorn app.api:app --reload

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

lint:
	uv run ruff check app tests

clean:
	find . -name "__pycache__" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".ruff_cache" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
