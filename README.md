# SDR Bot — Sistema Multi-Agente

Chatbot SDR (Sales Development Representative) construído com **Google
ADK**, com orquestração multi-agente, mascaramento de PII, guardrails,
RAG avaliado, observabilidade (Phoenix + LangFuse) e um eval set com
LLM-as-judge.

Para detalhes de design, trade-offs e bugs encontrados em cada parte,
ver [ARCHITECTURE.md](ARCHITECTURE.md).

## Como rodar

### 1. Pré-requisitos

```bash
# Instala o uv (gerenciador de projeto/dependências), se ainda não tiver
curl -LsSf https://astral.sh/uv/install.sh | sh

# Cria o ambiente virtual e instala tudo (runtime + dev) a partir do
# uv.lock, com versões travadas
uv sync
```

Não precisa ativar o `.venv` manualmente — use `uv run <comando>` (ex:
`uv run pytest`), que já roda dentro do ambiente certo.

### 2. Configurar variáveis de ambiente

```bash
cp .env.example .env
# edite .env e preencha OPENROUTER_API_KEY (https://openrouter.ai/keys)

# gere e preencha também PII_HASH_SALT (obrigatório -- sem ele, o
# mascaramento de CPF/CNPJ falha alto, de propósito, em vez de usar
# um salt inseguro por padrão):
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Subir o LiteLLM Proxy

```bash
cd litellm_proxy
docker compose up
```

Isso expõe um endpoint OpenAI-compatible em `http://localhost:4000`.

### 4. Rodar o bot

Em outro terminal, na raiz do projeto:

```bash
uv run python -m app.main
```

### 5. Rodar os testes

```bash
uv run pytest
```

## Atalhos com Makefile

```bash
make web    # proxy + adk web (UI de desenvolvimento do ADK)
make cli    # proxy + CLI (app/main.py)
make api    # proxy + FastAPI com --reload

make test            # suite completa, grátis
make test-live       # conversas douradas contra o LLM real (custa API)
make eval-run         # golden eval set + LLM-judge (custa API)
make lint             # ruff
```

Rode `make` (sem alvo) ou `make help` pra ver a lista completa.

## Status do roadmap

As 6 partes planejadas (esqueleto, PII, guardrails, RAG, observabilidade,
eval) estão implementadas. Fora de escopo deliberadamente: calendário
real (`book_meeting` é mock hoje), integração com CRM real, e
autenticação/multi-tenant na API.

## CI/CD e deploy

Pipeline no GitHub Actions com deploy automático pra Cloud Run em push
para `main`, infraestrutura provisionada via Terraform. Ver a seção
[CI/CD e deploy na GCP](ARCHITECTURE.md#cicd-github-actions-e-deploy-na-gcp)
em ARCHITECTURE.md.
