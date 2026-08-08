# SDR Bot — Sistema Multi-Agente (Partes 1 e 2: Esqueleto + PII)

Chatbot SDR (Sales Development Representative) construído com **Google ADK**,
desenhado para demonstrar, na prática, os requisitos técnicos de vagas de
LLM/AI Engineer sênior focadas em produção: orquestração multi-agente,
guardrails, mascaramento de PII, RAG avaliado e observabilidade.

Este projeto é dividido em partes incrementais. Este README cobre as
**Partes 1 e 2**.

## O que existe nesta parte

- Hierarquia multi-agente real no ADK: 1 `OrchestratorAgent` (raiz) +
  5 agentes especialistas (`QualificationAgent`, `KnowledgeAgent`,
  `ObjectionHandlingAgent`, `SchedulingAgent`, `EscalateToHumanAgent`),
  consultados via **AgentTool** — não via `sub_agents`/`transfer_to_agent`
  (ver docstring de `app/agents/orchestrator.py` para o porquê: usar
  `sub_agents` causou dois bugs reais de transferência não intencional
  entre agentes durante testes manuais, rastreados a issues abertas do
  ADK — google/adk-python#1038 e #3850).
- O Orchestrator decide qual especialista **consultar** (como uma função)
  com base na `description` de cada um, recebe a resposta de volta, e
  permanece no controle da conversa em todo turno — nenhum especialista
  assume a conversa permanentemente.
- Estado compartilhado (`session.state`) com contrato documentado em
  `app/agents/session/state_schema.py`.
- Camada de modelo desacoplada: cada agente usa `LiteLlm` apontando para
  um **LiteLLM Proxy self-hosted**, que por sua vez roteia para modelos na
  **OpenRouter** — ver `litellm_proxy/config.yaml` para o mapeamento
  modelo-por-agente e a lógica de fallback.
- Um agente com tools reais (`SchedulingAgent`), para validar tool-calling
  ponta a ponta através do proxy antes de mexer em tools mais sensíveis.
- **Mascaramento de PII (Parte 2)**: Presidio + recognizers customizados
  para CPF/CNPJ (com validação real de dígito verificador) + telefone BR
  (via `phonenumbers`) + spaCy `pt_core_news_lg` para nomes — ver seção
  dedicada abaixo.
- Smoke tests da topologia + testes de PII (`tests/`) que rodam sem
  precisar de chave de API (a única exceção é o download do modelo spaCy
  no `uv sync`, que acontece uma vez).

## O que **não** está aqui ainda (de propósito)

| Falta | Onde entra |
|---|---|
| Guardrails completos / mitigação de prompt injection | Parte 3 |
| RAG real + MCP Server para o KnowledgeAgent | Parte 4 |
| LangFuse + Phoenix (observabilidade) | Parte 5 |
| Golden eval set + LLM-as-judge + regressão | Parte 6 |

Cada agente tem comentários `TODO Parte N` no código exatamente nos pontos
onde essas camadas vão se conectar — não são promessas soltas, são pontos
de extensão já identificados na arquitetura.

## Como rodar

### 1. Pré-requisitos

```bash
# Instala o uv (gerenciador de projeto/dependências), se ainda não tiver
curl -LsSf https://astral.sh/uv/install.sh | sh

# Cria o ambiente virtual e instala tudo (runtime + dev) a partir do
# uv.lock, com versões travadas — reprodutível, não "funciona na minha
# máquina"
uv sync
```

Não precisa ativar o `.venv` manualmente — use `uv run <comando>` (ex:
`uv run pytest`, `uv run python -m app.main`), que já roda dentro do
ambiente certo.

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

Isso expõe um endpoint OpenAI-compatible em `http://localhost:4000`, que
roteia cada alias (`orchestrator-model`, `qualification-model`, etc.) para
o modelo real configurado em `config.yaml` na OpenRouter.

Alternativa sem Docker:

```bash
uvx --from 'litellm[proxy]' litellm --config litellm_proxy/config.yaml --port 4000
```

### 4. Rodar o bot

Em outro terminal, na raiz do projeto:

```bash
uv run python -m app.main
```

Exemplo de conversa esperada — note que o autor exibido é sempre
`OrchestratorAgent` agora (ele consulta o especialista internamente via
AgentTool e entrega a resposta final; ver trade-off documentado em
`app/agents/orchestrator.py`):

```
Você: Oi, vi vocês no LinkedIn
[OrchestratorAgent] Oi! Que bom que você chegou até a gente...

Você: quanto custa o plano?
[OrchestratorAgent] Sobre os planos...
```

### 6. Interface visual do ADK (`adk web`)

O ADK inclui uma UI de desenvolvimento que mostra a árvore de agentes, o
histórico de eventos turno a turno, e o payload exato de cada chamada de
tool (nome, argumentos, retorno) — útil sobretudo para depurar problemas
de tool-calling sem precisar ler traceback.

```bash
uv run adk web app/agents
```

Abra `http://127.0.0.1:8000` no navegador. O agente aparece na UI com o
nome `agents` (nome da pasta) — isso é esperado, não é o nome de nenhum
agente nosso especificamente.

**Detalhe não-óbvio, documentado aqui porque nos custou tempo depurando**:
o ADK decide como escanear a pasta baseado numa convenção específica —
`is_single_agent_directory()` (em `google/adk/cli/utils/agent_loader.py`)
procura por um arquivo chamado literalmente `agent.py` (ou
`root_agent.yaml`) diretamente na pasta apontada. Sem isso, o ADK assume
que a pasta é um **diretório pai contendo vários agentes** e escaneia
*suas subpastas* como se cada uma fosse um agente separado — no nosso
caso, isso faria o ADK escanear `config/` e `session/` (que não têm
`root_agent`) em vez do próprio pacote `agents`, e a UI aparecia vazia,
sem nenhum erro explícito.

É por isso que existe `app/agents/agent.py` — um arquivo pequeno,
somente com `from .orchestrator import root_agent`, cuja única função é
satisfazer essa convenção. É também o motivo de `config/` e `session/`
estarem aninhados dentro de `app/agents/` (não como pastas irmãs de
`app/agents/`): o ADK isola a pasta apontada como raiz de import sem
visibilidade nenhuma para pastas irmãs via import relativo — então tudo
que os agentes precisam importar precisa estar dentro da própria pasta
que o `adk web` aponta.

### 7. Rodar os testes

```bash
uv run pytest
```

## Arquitetura (visão desta parte)

```
Usuário (CLI)
      │
      ▼
OrchestratorAgent (LlmAgent, tools=[AgentTool(...), ...])
      │  consulta o especialista certo com base na description,
      │  recebe a resposta de volta, permanece no controle
      ├── QualificationAgent
      ├── KnowledgeAgent        (RAG real chega na Parte 4)
      ├── ObjectionHandlingAgent
      ├── SchedulingAgent        (único com tools nesta parte)
      └── EscalateToHumanAgent
      │
      ▼  model=LiteLlm(model="litellm_proxy/<alias>", api_base=..., api_key=...)
LiteLLM Proxy (Docker, litellm_proxy/config.yaml)
      │  resolve alias -> modelo real + fallback
      ▼
OpenRouter ──► Claude 3.5 Sonnet / GPT-4o-mini / Llama 3.1 (fallback)
```

## CI/CD (GitLab) e deploy na GCP

### Modelo de branches

```
feature branches ──MR──► develop ──MR──► main
   (trabalho acontece)   (integração,      (só deploy — nada mais)
                          default branch
                          do repositório)
```

- **`develop`** é a branch padrão do repositório (configurar em Settings
  → Repository → Default branch). Toda feature branch abre MR contra
  ela. `lint`/`test`/`docker_build_check` rodam em qualquer MR e em todo
  push pra `develop` — feedback rápido, sem tocar em nada de GCP.
- **`main`** só recebe merge vindo de `develop`, quando o conjunto de
  mudanças está pronto pra ir pro ar. É a **única** branch que os jobs
  `build_and_push`/`deploy_*` reconhecem — um push direto em `develop`
  nunca aciona deploy, só em `main`.
- Deliberadamente **não** é GitFlow completo (sem release/hotfix
  branches) — pra um projeto deste porte, esse processo extra não paga
  o custo de manutenção.
- Recomendado: proteger `main` em Settings → Repository → Protected
  branches (só merge via MR, sem push direto).

Isso é o motivo de `.gitlab-ci.yml` usar nomes de branch explícitos
(`"develop"`, `"main"`) nas regras, em vez de `$CI_DEFAULT_BRANCH` — uma
vez que "branch padrão" e "branch que decide deploy" são conceitos
diferentes aqui, uma variável só não cobre os dois.

### Pipeline

O pipeline (`.gitlab-ci.yml`) é evolutivo, em duas camadas:

1. **Sempre roda, sem credencial nenhuma**: `lint`, `test` (smoke tests,
   sem chamada real de LLM) e `docker_build_check` (valida que os
   Dockerfiles buildam) — em qualquer MR e em push pra `develop` ou
   `main`. Isso mantém o pipeline verde desde o primeiro commit, mesmo
   antes de qualquer configuração de nuvem.
2. **Só aparece quando a GCP estiver configurada E o commit for em
   `main`**: `build_and_push` (Artifact Registry) e os dois `deploy_*`
   (Cloud Run), condicionados à variável `$GCP_PROJECT_ID` existir no
   projeto GitLab.

Arquitetura de deploy: dois serviços Cloud Run — `litellm-proxy` (o
gateway pra OpenRouter) e `sdr-bot-api` (a API FastAPI sobre o sistema de
agentes), o segundo apontando pro primeiro via `LITELLM_PROXY_URL`.

### Configurando deploy na GCP (rodar uma vez, fora do pipeline)

```bash
# 1. Habilitar APIs necessárias
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
    iamcredentials.googleapis.com secretmanager.googleapis.com

# 2. Criar repositório no Artifact Registry
gcloud artifacts repositories create sdr-bot-repo \
    --repository-format=docker --location=us-central1

# 3. Criar service account que o pipeline vai impersonar
gcloud iam service-accounts create gitlab-ci-deployer \
    --display-name="GitLab CI/CD deployer"

# Dar as permissões mínimas necessárias (Artifact Registry + Cloud Run)
gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:gitlab-ci-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:gitlab-ci-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/run.admin"
gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
    --member="serviceAccount:gitlab-ci-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountUser"

# 4. Criar o Workload Identity Pool + Provider pro GitLab
gcloud iam workload-identity-pools create gitlab-pool \
    --location="global" --display-name="GitLab CI"

gcloud iam workload-identity-pools providers create-oidc gitlab-provider \
    --location="global" --workload-identity-pool="gitlab-pool" \
    --issuer-uri="https://gitlab.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.project_path" \
    --attribute-condition="assertion.project_path == '<seu-namespace>/<seu-repo>'"

# 5. Permitir que a identidade federada do GitLab impersone a service account
gcloud iam service-accounts add-iam-policy-binding \
    "gitlab-ci-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/gitlab-pool/attribute.repository/<seu-namespace>/<seu-repo>"

# 6. Guardar os segredos de runtime no Secret Manager (não em CI/CD variables)
echo -n "sua-chave-openrouter" | gcloud secrets create openrouter-api-key --data-file=-
echo -n "sua-master-key-do-proxy" | gcloud secrets create litellm-proxy-key --data-file=-
```

### Variáveis a configurar no GitLab (Settings > CI/CD > Variables)

| Variável | Valor |
|---|---|
| `GCP_PROJECT_ID` | ID do projeto GCP |
| `GCP_PROJECT_NUMBER` | Número do projeto (`gcloud projects describe`) |
| `GCP_REGION` / `AR_REGION` | ex: `us-central1` |
| `AR_REPOSITORY` | `sdr-bot-repo` |
| `WIF_POOL_ID` | `gitlab-pool` |
| `WIF_PROVIDER_ID` | `gitlab-provider` |
| `WIF_SERVICE_ACCOUNT` | `gitlab-ci-deployer@<project-id>.iam.gserviceaccount.com` |

Nenhuma chave JSON de service account é armazenada em lugar nenhum — a
autenticação usa o ID token OIDC que o próprio GitLab emite por job
(`id_tokens` no `.gitlab-ci.yml`), trocado por uma credencial federada de
curta duração via `gcloud iam workload-identity-pools create-cred-config`.

## Mascaramento de PII (Parte 2)

### Onde a máscara acontece — desenho de "perímetro"

O Orchestrator é o único ponto de entrada (recebe texto cru do usuário)
e o único ponto de saída (entrega a resposta final) de todo o sistema
— consequência direta da migração pra `AgentTool` na Parte 1. Por isso:

- **Todo agente** (Orchestrator + 5 especialistas) registra `mask_pii`
  em `before_model_callback` — mascarar em texto já mascarado é
  idempotente (não-operação), então isso é defesa em profundidade
  barata, não redundância real hoje. Importa quando a Parte 4 der a
  algum especialista uma tool que traga dado de fora (ex: CRM).
- **Só o Orchestrator** registra `unmask_pii` em `after_model_callback`
  — desmascarar em qualquer outro lugar arriscaria PII crua voltando
  pro contexto do Orchestrator antes da resposta final estar pronta.

```
Usuário (texto cru, pode ter PII)
      │
      ▼
OrchestratorAgent.before_model_callback  ← mask_pii (entrada)
      │  (a partir daqui, nenhum LLM do sistema vê PII crua)
      ▼
Orchestrator decide consultar um especialista (AgentTool)
      │
      ▼
Specialist.before_model_callback  ← mask_pii (defesa em profundidade,
      │                               normalmente não-operação)
      ▼
Specialist responde (ainda mascarado)
      │
      ▼
Resultado volta pro Orchestrator como retorno de tool (ainda mascarado)
      │
      ▼
OrchestratorAgent.after_model_callback  ← unmask_pii (só aqui)
      │
      ▼
Usuário recebe o nome real, nunca um token
```

### As três camadas

| Camada | Entidades | Ação | Por quê |
|---|---|---|---|
| 1 — Token reversível | `PERSON`, `EMAIL_ADDRESS`, `TELEFONE_BR` | `[PERSON_1]`, `[EMAIL_1]`... — mapa em `session.state`, revertido só na saída | Útil pra conversa soar natural |
| 2 — Hash com salt | `CPF_BR`, `CNPJ_BR` | SHA-256 + salt fixo secreto, nunca revertido | O bot nunca precisa "falar" um CPF de volta — só correlacionar |
| 3 — Bloqueio total | `CREDIT_CARD` | Mensagem nunca chega ao LLM; resposta de recusa curto-circuitada | Não existe motivo legítimo pra dado de cartão numa conversa de SDR |

**Detalhe de segurança que importa citar em entrevista**: hash de CPF
sem salt secreto não protege quase nada — 11 dígitos é um espaço de
busca pequeno o suficiente pra força bruta trivial. O salt em
`PII_HASH_SALT` precisa ser fixo (pra permitir correlação entre
sessões: "é o mesmo lead de antes?") **e** secreto (fora do código,
via `.env` local / Secret Manager em produção) — um hash sem essas duas
propriedades juntas não é proteção de verdade.

### Recognizers customizados vs. built-in do Presidio

- `CPF_BR`, `CNPJ_BR`: customizados (`app/agents/pii/recognizers.py`),
  com validação real de dígito verificador — um número no formato de
  CPF que falha o dígito verificador não é tratado como PII (evita
  falso positivo em qualquer ID de 11 dígitos). Também rejeita
  explicitamente sequências tipo `111.111.111-11`, que passam no
  checksum matematicamente mas nunca são CPFs reais.
- `TELEFONE_BR`: built-in do Presidio (`PhoneRecognizer`, usa
  `python-phonenumbers`), só reconfigurado pra região `BR` — mais
  robusto que regex escrita à mão.
- `CREDIT_CARD`: built-in do Presidio, mas exige atenção — o
  recognizer padrão tem `supported_language="en"` e é **silenciosamente
  descartado** ao carregar recognizers pra português (só loga um
  warning, não falha). Descoberto via teste automatizado, corrigido
  registrando-o explicitamente com `supported_language="pt"` em
  `engine.py`.
- `PERSON`: built-in do Presidio, mas com o backend de NLP trocado pra
  `pt_core_news_lg` (spaCy) — o padrão do Presidio é treinado em
  inglês e não reconhece nomes em português de forma confiável.

### Rodando os testes de PII isoladamente

```bash
uv run pytest tests/test_pii_masking.py -v
```

A primeira execução carrega o modelo spaCy (~15s); chamadas seguintes
na mesma sessão de teste reusam o engine cacheado (singleton em
`engine.py`).

## Próxima parte

**Parte 3**: guardrails mais completos e mitigação de prompt injection —
generaliza o padrão já usado em `_guardrails.py` (bloquear comportamento
indesejado via callback) para cobrir tentativas de manipulação de
prompt, allowlist de ações sensíveis (ex: `book_meeting` só deveria
confirmar se o lead está qualificado), e validação de saída contra
políticas de negócio (ex: nunca oferecer desconto não autorizado).
