# Arquitetura — SDR Bot

Este documento cobre o design técnico do projeto: por que cada decisão
foi tomada, os trade-offs considerados, e os bugs reais encontrados
construindo cada parte. Para instalar e rodar o projeto, ver
[README.md](README.md).

O projeto é dividido em partes incrementais (1–6): esqueleto multi-agente,
mascaramento de PII, guardrails, RAG, observabilidade e eval sistemático.
O que fica deliberadamente fora do escopo (não é "falta", é um limite
consciente de portfólio) é o que qualquer sistema real de produção teria
por trás de uma integração paga/regulada: calendário real (hoje
`check_availability`/`book_meeting` são mock), um CRM de verdade recebendo
o `opportunity_brief`, e autenticação/multi-tenant na API.

## Visão geral

```
Usuário (CLI)
      │
      ▼
OrchestratorAgent (LlmAgent, tools=[AgentTool(...), ...])
      │  consulta o especialista certo com base na description,
      │  recebe a resposta de volta, permanece no controle
      ├── QualificationAgent
      ├── KnowledgeAgent        (RAG real, ver Parte 4)
      ├── ObjectionHandlingAgent
      ├── SchedulingAgent        (tools reais de agendamento)
      └── EscalateToHumanAgent
      │
      ▼  model=LiteLlm(model="litellm_proxy/<alias>", api_base=..., api_key=...)
LiteLLM Proxy (Docker, litellm_proxy/config.yaml)
      │  resolve alias -> modelo real + fallback
      ▼
OpenRouter ──► Claude Sonnet / GPT-4o-mini / Llama 3.1 (fallback)
```

- Hierarquia multi-agente real no ADK: 1 `OrchestratorAgent` (raiz) +
  5 agentes especialistas, consultados via **AgentTool** — não via
  `sub_agents`/`transfer_to_agent` (ver docstring de
  `app/agents/orchestrator.py` para o porquê: usar `sub_agents` causou
  dois bugs reais de transferência não intencional entre agentes durante
  testes manuais, rastreados a issues abertas do ADK —
  google/adk-python#1038 e #3850).
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

### `adk web` — convenção de pasta que nos mordeu

O ADK decide como escanear a pasta baseado numa convenção específica —
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
que o `adk web` aponta. Essa mesma restrição volta a aparecer nas
decisões de `mcp_server/`/`ingestion/` (Parte 4) e `observability.py`
(Parte 5) abaixo.

## Mascaramento de PII (Parte 2)

### Onde a máscara acontece — desenho de "perímetro"

O Orchestrator é o único ponto de entrada (recebe texto cru do usuário)
e o único ponto de saída (entrega a resposta final) de todo o sistema
— consequência direta da migração pra `AgentTool` na Parte 1. Por isso:

- **Todo agente** (Orchestrator + 5 especialistas) registra `mask_pii`
  em `before_model_callback` — mascarar em texto já mascarado é
  idempotente (não-operação), então isso é defesa em profundidade
  barata, não redundância real hoje. Importa quando algum especialista
  ganha uma tool que traga dado de fora (ex: CRM).
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

## Guardrails (Parte 3)

Três pontos deixaram rastro explícito de TODO no código durante as
Partes 1 e 2 — a Parte 3 é sobre fechar exatamente esses três.

### 1. Detecção de prompt injection (`before_model_callback`)

Escopo desta versão: **só heurística** (regex/keyword), sem camada de
LLM-judge — decisão deliberada de custo/latência, não limitação técnica.
Roda depois do `mask_pii` na mesma cadeia
(`[mask_pii, detect_prompt_injection]`), em todo agente:

```python
before_model_callback=[mask_pii, detect_prompt_injection]
```

Só avalia o **último turno do usuário**, não o histórico inteiro a cada
chamada — uma mensagem já filtrada não precisa ser reavaliada pra
sempre. Ver `app/agents/guardrails/prompt_injection.py`.

### 2. Allowlist de ação (`before_tool_callback`)

`book_meeting` só executa de verdade se
`session.state[STATE_QUALIFICATION_STATUS] == "qualified"`. Isso é
diferente de um guardrail de conteúdo — é sobre o que o sistema tem
permissão de **executar**, não sobre o que ele diz:

```
book_meeting(slot) chamado
      │
      ▼
enforce_action_allowlist verifica qualification_status
      │
   ┌──┴──┐
  sim    não
   │      │
   ▼      ▼
executa   retorna {"status": "blocked", "error_message": "...link..."}
```

Quando bloqueado, a resposta simula um redirecionamento (link fake —
não existe formulário de qualificação real neste projeto) em vez de só
recusar. O contrato de retorno imita o padrão de erro que `book_meeting`
já usava pra "horário indisponível", deixando o modelo do
`SchedulingAgent` transformar isso em linguagem natural, em vez da
guardrail hardcodar a frase exata.

Isso também exigiu resolver um problema real: `STATE_QUALIFICATION_STATUS`
existia como chave reservada desde a Parte 1, mas nada escrevia um valor
estruturado nela. A `QualificationAgent` ganhou uma tool nova pra isso:

```python
set_qualification_status(
    status: "qualified"|"disqualified"|"in_progress",
    pain: str,
    product_of_interest: str,
    reasoning: str,
    company_size: str | None = None,
    additional_notes: str | None = None,
)
```

Mesmo padrão que `SchedulingAgent` já usava — mudança de estado
estruturada e auditável via tool, não texto livre que outro lugar do
sistema teria que tentar interpretar. Além de `status`, a tool grava um
"opportunity brief" completo em `qualification_notes` (`pain`,
`product_of_interest`, `company_size`, `reasoning`, `additional_notes`)
— é isso que dá a quem assume a conversa depois (hoje, `book_meeting`
via `SchedulingAgent`; um Closer/CRM real amanhã) contexto sobre a
oportunidade sem precisar reler o chat inteiro.

### 3. Validação de política de saída (`after_model_callback`)

"Nunca ofereça desconto" era só uma instrução de prompt — contornável
por prompt injection. Agora também é verificado na resposta de verdade:

```python
after_model_callback=[validate_output_policy, block_unauthorized_transfer]  # especialistas
after_model_callback=[validate_output_policy, unmask_pii]                    # orchestrator
```

**Escopo**: todo agente (defesa em profundidade), não só
`ObjectionHandlingAgent` — mesma postura da Parte 2. Hoje só o
`ObjectionHandlingAgent` fala sobre desconto, mas `KnowledgeAgent`
discute preço de verdade a partir da Parte 4, e essa proteção já
precisava estar no lugar antes disso, não adicionada depois.

### Onde o código mora

```
app/agents/guardrails/
├── transfer.py           # bloqueio de transfer_to_agent não autorizado
│                           # (existia desde a Parte 1 como _guardrails.py,
│                           # movido pra cá — não é mais um stopgap solto)
├── prompt_injection.py   # detect_prompt_injection
├── output_policy.py      # validate_output_policy
└── action_allowlist.py   # enforce_action_allowlist
```

### Rodando os testes de guardrails isoladamente

```bash
uv run pytest tests/test_prompt_injection.py tests/test_output_policy.py \
  tests/test_action_allowlist.py tests/test_guardrails.py -v
```

## Estratégia de testes

Pergunta prática: "editei `qualification.py`, como sei que não quebrei
nada?" A resposta muda dependendo de QUÃO CARO você aceita que a
resposta seja — por isso os testes deste projeto ficam em 4 camadas,
cada uma com um trade-off diferente de custo vs. o que ela pega:

| Camada | Custo | Pega | Onde |
|---|---|---|---|
| 1. Estrutural | Grátis, instantâneo | Wiring quebrado, tool faltando, import errado | `test_smoke.py` |
| 2. Lógica de tool (Python puro) | Grátis, instantâneo | A parte determinística de uma tool está errada | `test_tool_logic.py` |
| 3. Conversa dourada | Barato, chamadas reais de LLM | Agente parou de chamar a tool certa, parou de completar o fluxo, guardrail disparou sem motivo | `test_golden_conversations.py` |
| 4. Eval set com LLM-judge | Custo real, minutos | Qualidade da resposta regrediu, não só a estrutura | `eval/` (Parte 6), `make eval-run` |

**Camadas 1 e 2 rodam em `make test`** (e no pipeline de CI, sempre) —
não custam nada, então não tem motivo pra não rodar toda vez.

**Camada 3 é deliberadamente separada** (`make test-live`), porque
custa chamadas reais de API. Ela verifica ESTRUTURA (qual chave de
`session.state` foi escrita, qual guardrail disparou), nunca texto
exato — isso é o que permite ela sobreviver a ajustes de prompt sem
precisar ser reescrita toda hora:

```bash
make test-live
# ou, sem o Makefile:
RUN_LIVE_TESTS=1 uv run pytest tests/test_golden_conversations.py -v
```

**Camada 4** (golden eval set + LLM-as-judge + regressão versionada,
`eval/`, Parte 6) é deliberadamente mais cara e mais lenta que a camada
3 — ela existe pra responder "a qualidade da resposta melhorou ou
piorou?" com um número, não pra dar feedback rápido durante edição de
prompt. Ver seção dedicada abaixo.

### Workflow prático ao editar um agente

1. `uv run pytest` (grátis) — confirma que nada estrutural quebrou
2. Se mexeu na lógica de uma tool, rode/adicione o teste direto dela
   (camada 2, grátis)
3. `make test-live` — confirma que o fluxo ainda completa e chama as
   tools certas (custa uma chamada real, mas é rápido)
4. `make web` — checagem manual de tom/qualidade da conversa (ainda
   importa, só não é mais a ÚNICA linha de defesa)
5. Antes de considerar uma mudança de prompt/modelo "pronta" pra
   produção, `make eval-run` — é isso que transforma "parece melhor" em
   uma métrica que sobe ou desce (ver Parte 6)

## RAG real (Parte 4)

### Arquitetura: por que MCP Server como processo separado

O agente ADK nunca importa `mcp_server/` ou `ingestion/` como módulo
Python — conecta no servidor MCP via subprocess/stdio
(`McpToolset(connection_params=StdioConnectionParams(...))`), do mesmo
jeito que o LiteLLM Proxy já é um processo separado desde a Parte 1.
Essa decisão não foi só estética: significa que `mcp_server/` e
`ingestion/` nunca entram na árvore de import de `app/agents/`, então
nunca podem reintroduzir a restrição de import-root isolado do
`adk web` que já nos mordeu duas vezes (Partes 1 e 2, ver acima).

```
KnowledgeAgent (app/agents/knowledge.py)
      │  McpToolset via subprocess/stdio
      ▼
mcp_server/server.py  (fastmcp, processo separado)
      │  lê
      ▼
mcp_server/chroma_data/  (ChromaDB, PersistentClient)
      ▲  escreve
      │
ingestion/assets.py  (Dagster: raw_docs → chunks → chroma_index)
      ▲  lê
      │
ingestion/knowledge_base/*.md  (8 documentos sobre a Helssing)
```

### Pipeline de ingestão (Dagster)

`raw_docs → chunks → chroma_index`, cada estágio um asset com linhagem
rastreada — Dagster foi escolhido sobre Prefect especificamente porque
o modelo de software-defined assets responde bem à pergunta "o índice
está desatualizado em relação aos documentos-fonte?" via linhagem, sem
código extra nosso.

**Chunking**: por seção de nível 2 (`##`), não por tamanho fixo — os
documentos já são estruturados em seções semanticamente coerentes (ver
`ingestion/knowledge_base/*.md`), então isso preserva significado
melhor que corte por N caracteres. Cada chunk carrega o título do
documento como prefixo, pra não perder contexto quando recuperado
isoladamente.

**Dois `asset_check`** em `chroma_index`, resposta real pro requisito
"RAG avaliado com dados":
- `retrieval_recall_at_k`: recall@3 contra um golden query set de 4
  perguntas — cada uma diz "essa pergunta deveria recuperar um chunk
  vindo deste documento". Validado rodando de verdade: recall@3 = 1.0.
- `no_pii_leaked_into_index`: reusa o Presidio já validado na Parte 2
  pra confirmar que a base de conhecimento (conteúdo de produto) nunca
  tem PII de verdade nela.

```bash
make ingest-dev   # abre a UI do Dagster -- materialize os 3 assets
```

A primeira materialização baixa o modelo de embedding multilingue
(`paraphrase-multilingual-MiniLM-L12-v2`, ~470MB) do HuggingFace Hub —
precisa de internet normal, sem restrição de rede. **O índice persiste
em disco** (`mcp_server/chroma_data/`, gitignored) — não precisa
rematerializar toda vez que o app roda, só quando o conteúdo dos `.md`
ou a lógica de chunking mudar.

### Escolha de modelo de embedding

`paraphrase-multilingual-MiniLM-L12-v2` (leve, ~470MB, multilingue,
bem estabelecido) em vez de encoders específicos de português como os
"Serafim" da PORTULAN (melhor qualidade, mas outro download pesado em
cima do modelo do spaCy que já carregamos na Parte 2) — trade-off
deliberado de simplicidade sobre qualidade máxima, mesmo espírito das
outras escolhas de modelo neste projeto.

### Bug real encontrado rodando isso: `on_model_error_callback`

Modelos Claude, através do LiteLLM, ocasionalmente emitem argumentos
de tool call como JSON duplicado e concatenado sem separador (ex:
`{"status": "x"}{"status": "x"}`) — bug real e ainda aberto a
montante ([BerriAI/litellm#20543](https://github.com/BerriAI/litellm/issues/20543)).
Descoberto porque `test_qualification_flow_sets_status` falhou duas
vezes seguidas com a mesma assinatura de erro — não foi um acaso raro.
ADK já tenta reparar formatos malformados antes de desistir
(`ast.literal_eval`, chaves sem aspas), mas nenhuma estratégia cobre
"JSON válido duplicado", então o erro original sobe e derruba o agente
inteiro.

`app/agents/guardrails/model_error_recovery.py` intercepta via
`on_model_error_callback` (wireado em todo agente, defesa em
profundidade) e degrada graciosamente — pede pro lead repetir a
mensagem, em vez de travar a conversa inteira por um bug de terceiros
que nem o LiteLLM conseguiu corrigir de forma definitiva ainda (a
tentativa de correção deles, #18667, foi revertida em #19243).

### Rodando os testes da Parte 4 isoladamente

```bash
uv run pytest tests/test_ingestion_chunking.py tests/test_mcp_server_tools.py \
  tests/test_model_error_recovery.py -v
```

Nenhum desses precisa do modelo de embedding real nem de rede —
`test_mcp_server_tools.py` usa um embedder falso e determinístico
(válido pra testar estrutura e pra `get_pricing_info`, que usa filtro
de metadado, não busca semântica; relevância semântica de verdade já
foi validada rodando o pipeline real).

## Observabilidade (Parte 5)

### Divisão de responsabilidade — por que dois sistemas, não um

Phoenix e LangFuse enxergam **camadas diferentes** do sistema, o que
evita que um duplique o outro:

```
Lead message
      │
      ▼
OrchestratorAgent → especialistas → MCP/RAG ──┐
      │                                        │  openinference-instrumentation-google-adk
      ▼                                        │  (automático: árvore de agentes, tool
LiteLLM Proxy ──────────────────────────────────┤  calls, retrieval do RAG)
      │                                        ▼
      ▼                                    Phoenix (self-hospedado, processo
OpenRouter → Anthropic/OpenAI                único, sem conta, serviço irmão)
      │
      ▼ (callback langfuse_otel, não o "legacy" success_callback)
LangFuse (self-hospedado: postgres + clickhouse +
          redis + minio + langfuse-web + langfuse-worker)
```

- **Phoenix** enxerga *o que o sistema está fazendo* — qual agente
  rodou, qual tool disparou, o que foi recuperado do ChromaDB pra uma
  pergunta específica (estende diretamente o trabalho de RAG da Parte
  4 — dá pra *ver* uma recuperação acontecendo numa conversa real, não
  só no `asset_check` do Dagster).
- **LangFuse** enxerga *quanto custou* — toda chamada de modelo que
  passa pelo proxy, num lugar só, independente de qual dos 6 agentes
  disparou.

> Esta seção descreve o setup LOCAL (`make phoenix-up`/`make
> langfuse-up`). Em produção, os dois rodam numa VM dedicada, não em
> Cloud Run -- ver "VM de observabilidade" na seção CI/CD abaixo pro
> porquê (Cloud Run e background export não combinam) e pro histórico
> completo de incidentes reais que levaram a essa decisão.

### Phoenix — instrumentação automática

Um `phoenix.otel.register(auto_instrument=True)` instrumenta
automaticamente a árvore de agentes inteira via
`openinference-instrumentation-google-adk` — sem precisar anotar cada
agente manualmente. Vive em `app/agents/observability.py`, seguindo o
mesmo padrão de "efeito colateral de import" de `pii/` (Parte 2) e
`guardrails/` (Parte 3): fica dentro de `app/agents/` porque o
`adk web` isola esse diretório como raiz de import (ver "adk web —
convenção de pasta" acima).

**Bug real encontrado testando isso de verdade**: o valor óbvio pro
endpoint (`http://localhost:6006`, a raiz da UI) falha com `405 Method
Not Allowed` — o endpoint de ingestão OTLP de verdade é
`http://localhost:6006/v1/traces`. Só descobri isso rodando um Phoenix
real neste ambiente, enviando um span de teste, vendo falhar, e
confirmando a correção consultando a própria API do Phoenix depois.

```bash
make phoenix-up   # sobe em http://localhost:6006, sem conta necessária
```

### LangFuse — self-hospedado, `langfuse_otel`

Self-hospedar LangFuse é, disparado, a stack mais pesada deste projeto
— 6 containers (postgres, clickhouse, redis, minio, langfuse-web,
langfuse-worker) contra 1 do `litellm_proxy`. Decisão deliberada de
manter consistência com o resto do projeto (tudo self-hospedado) em
vez de usar o free tier do LangFuse Cloud, que seria bem mais simples.

O callback usado é `langfuse_otel` (`litellm_proxy/config.yaml`), não
o mais óbvio `success_callback: ["langfuse"]` — o próprio LiteLLM
documenta esse último como a integração "legacy v2 SDK", não
recomendada pro LangFuse v3+ (que é o que rodamos). `langfuse_otel`
constrói o endpoint OTLP e a autenticação (Basic Auth) automaticamente
a partir de `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_OTEL_HOST`.

**Duas descobertas reais rodando isso:**

1. `litellm_proxy/` e `langfuse/` são dois PROJETOS docker-compose
   separados (redes Docker diferentes por padrão) — `litellm-proxy`
   não alcança `langfuse-web` por nome de serviço sem ajuda.
   `extra_hosts: host.docker.internal:host-gateway` em
   `litellm_proxy/docker-compose.yml` resolve isso — inclusive no
   Linux, onde `host.docker.internal` não existe nativamente (diferente
   de Mac/Windows).
2. O `DATABASE_URL` que o LangFuse usa pra conectar no Postgres tinha,
   na minha primeira versão, um valor padrão (`postgres:postgres`)
   diferente da senha real configurada no container do Postgres
   (`LANGFUSE_POSTGRES_PASSWORD`) — um bug silencioso que só apareceria
   como falha de conexão. Corrigido derivando `DATABASE_URL` direto do
   mesmo segredo, eliminando a duplicação.

```bash
make langfuse-secrets   # gera os ~10 segredos necessários, só imprime
# cole no .env, depois:
make langfuse-up        # sobe em http://localhost:3000
```

### Anotações de guardrail nos traces

Além da instrumentação automática, os 6 guardrails (Partes 2 e 3)
anotam o span ATIVO do OpenTelemetry quando disparam —
`app/agents/observability.py::annotate_current_span()` usa
`add_event()` (não `set_attribute()`) de propósito: um disparo de
guardrail é uma ocorrência pontual dentro do span, não uma propriedade
persistente dele, e eventos aparecem na timeline do trace no Phoenix.

Eventos anotados: `guardrail.pii.masked` / `guardrail.pii.blocked`,
`guardrail.prompt_injection.blocked`, `guardrail.output_policy.blocked`,
`guardrail.action_allowlist.denied`, `guardrail.model_error.recovered`,
`guardrail.transfer.blocked`.

**Cuidado deliberado**: o evento de PII anota os TIPOS de entidade
detectados (`entity_types=PERSON,EMAIL_ADDRESS`), nunca os valores
mascarados — vazar o próprio dado mascarado pro trace anularia o
propósito inteiro de mascarar. Testado explicitamente
(`test_pii_masking_annotates_span_with_entity_types_not_values`).

Verificado que a propagação de contexto funciona de verdade — não só
assumido: `get_current_span()` chamado de dentro de uma função comum
aninhada (exatamente a forma como nossos callbacks de guardrail
executam) retorna o span ATIVO de verdade, não um span NoOp,
confirmado com um `TracerProvider` real e `InMemorySpanExporter` nos
testes.

### Rodando os testes da Parte 5 isoladamente

```bash
uv run pytest tests/test_observability_spans.py -v
```

Não precisa de Phoenix nem LangFuse rodando -- usa um `TracerProvider`
local isolado por teste, não o global registrado por
`app/agents/observability.py`.

## Eval set + LLM-as-judge (Parte 6)

Fecha o requisito "processo de avaliação sistemático, prova que uma
mudança melhorou, não acha". A camada 3 de testes (`test-live`) já
verifica ESTRUTURA (qual tool foi chamada, qual chave de estado foi
escrita); a Parte 6 mede QUALIDADE de verdade, com um critério
explícito por item e rastreável ao longo do tempo.

### Decisão de arquitetura: reusar o LangFuse já self-hospedado

Em vez de construir um mecanismo de persistência de avaliação
separado, esta parte reusa o LangFuse da Parte 5, que já tem suporte
nativo a **Datasets** (o golden eval set) e **Scores** (os resultados
de avaliação) — a UI dele já mostra tendência ao longo do tempo sem
código extra nosso. `dataset.run_experiment(...)` (SDK Python do
LangFuse) é o mecanismo central: dado um dataset e uma função de task,
ele roda a task pra cada item, aplica os evaluators fornecidos, e
publica tanto os resultados quanto os scores como uma "run" nomeada do
dataset, visível na UI.

```
eval/golden_set.py (22 itens, versionado em git -- fonte de verdade)
      │  eval/langfuse_client.py::ensure_golden_dataset (upsert por id)
      ▼
LangFuse Dataset "sdr-bot-golden-set"
      │  dataset.run_experiment(task=..., evaluators=[...])
      ▼
Pra cada item: eval/runner.py::run_conversation roda a conversa contra
o agente real (Runner + InMemorySessionService -- mesmo padrão de
tests/test_golden_conversations.py::_run_conversation)
      │
      ├─▶ eval/runner.py::check_structural -- mesma checagem
      │    determinística da camada 3 (chave de session.state, ou
      │    substring em guardrail_flags), sem custar LLM
      │
      └─▶ eval/judge.py::judge_response -- judge-model (LiteLLM Proxy)
           pontua tom/aderência ao critério/faithfulness, 1-5 por
           dimensão + justificativa (nunca um número solto)
      │
      ▼ Evaluation(s) devolvidos ao SDK -> LangFuse submete os Scores,
        anexados ao trace de cada item da run, automaticamente
      ▼
eval/assets.py::eval_regression (asset_check Dagster) compara a média
combinada desta run contra eval/baseline.json -- falha se caiu mais
que o threshold (ver eval/regression.py)
```

### Por que golden_set.py, não a UI do LangFuse, é a fonte de verdade

O dataset "sdr-bot-golden-set" no LangFuse é uma **projeção** de
`eval/golden_set.py` (`ensure_golden_dataset` faz upsert por `id` toda
vez que o pipeline roda), nunca o contrário. Um critério de sucesso é
uma decisão de produto/comportamento — merece review de PR como
qualquer mudança de código, o que editar direto numa UI não dá.

### Duplo critério por item, não só "o juiz achou bom"

Cada um dos 22 itens (`eval/golden_set.py`, 6 categorias do roadmap:
qualidade de qualificação, faithfulness do RAG, tom de objeção,
correção de guardrail, aderência de persona, escalonamento correto)
carrega dois critérios independentes:

- **Estrutural** (`expected_state_keys`/`expected_state_values`/
  `forbidden_state_keys`/`expected_guardrail_flag_substrings`):
  determinístico, o mesmo tipo de checagem que a camada 3 já faz.
  `GoldenSetItem` recusa (`pydantic.ValidationError`) qualquer item que
  não declare pelo menos um sinal estrutural — um item "só vibe",
  avaliado apenas pelo juiz, não é aceito.
- **Qualitativo** (`qualitative_criteria`, linguagem natural): cobre o
  que nenhuma chave de estado consegue — tom, se a resposta soa como
  interrogatório, se uma alegação de produto realmente vem do
  `reference_context` fornecido (só preenchido nos itens de
  `rag_faithfulness`, com trechos reais de `ingestion/knowledge_base/`).

Uma resposta que falha o critério ESTRUTURAL tem a nota do juiz
limitada a no máximo 2/5 (`eval/assets.py::_STRUCTURAL_FAILURE_SCORE_CAP`)
mesmo se o tom estiver ótimo — uma resposta educada que confirma um
agendamento sem qualificação, por exemplo, não é "boa" só porque soa
bem.

### `judge-model`: por que um modelo diferente dos avaliados

`litellm_proxy/config.yaml` ganhou o alias `judge-model`, apontando
pra `openai/gpt-4o` — na época, deliberadamente de uma família
diferente de `qualification-model`/`knowledge-model`/`objection-model`
(todos `claude-sonnet-5`). Um modelo julgando a própria família de
saída como boa é um viés de auto-avaliação documentado em LLM-as-judge.

A primeira escolha tinha sido `claude-opus-5` (mesma família Anthropic,
uma classe de raciocínio acima do avaliado) — trocado pra `gpt-4o`
depois de rodar `make eval-run` de verdade: Opus é bem mais caro/lento
(pressão real sobre o saldo da OpenRouter, que já bloqueou a run com
402 nesta parte), e `gpt-4o` já era uma família totalmente diferente da
Sonnet (elimina o viés igual ou melhor) com reputação sólida de seguir
formato JSON estrito — relevante porque uma falha real de parsing
apareceu num item durante teste (ver descoberta abaixo). `gpt-4o-mini`
(mais barato ainda) foi descartado de propósito NA ÉPOCA: já era o
modelo por trás de `orchestrator-model`/`scheduling-model`/
`escalate-model`, reintroduzindo viés de auto-avaliação pra qualquer
conversa que passe por eles.

**Atualização (custo) — essa propriedade está parcialmente quebrada
agora**: `qualification-model`/`knowledge-model`/`objection-model`
foram trocados de `claude-sonnet-5` pra `gpt-4o-mini` (Sonnet 5
dominava o gasto na OpenRouter mesmo sendo só 3 dos 7 aliases). Em
produção real, `knowledge-model` em `gpt-4o-mini` passou a narrar a
intenção de chamar uma tool ("Realizando busca por funcionalidades do
produto...") como se fosse a resposta final, em vez de chamar a tool
de verdade e responder com o resultado — sintoma clássico de
disciplina de tool-use mais fraca em modelos menores, não um bug de
infra. `knowledge-model` foi revertido pra `claude-sonnet-5` por causa
disso; `qualification-model`/`objection-model` continuam em
`gpt-4o-mini` (não mostraram o sintoma). Isso significa que 5 dos 6
agentes avaliados usam `gpt-4o-mini`, e o juiz (`gpt-4o`) é da MESMA
família GPT-4o — o viés que essa escolha existia pra evitar, embora a
dimensão mais sensível a alucinação (`faithfulness`, avaliada sobre a
saída do `knowledge-model`) já não compartilhe família com o juiz.
Não resolvido pros outros 5: se o viés de auto-avaliação importar pros
seus resultados, troque `judge-model` pra fora da família GPT-4o antes
de confiar num `make eval-run`. Ver TODO em `litellm_proxy/config.yaml`.

`eval/judge.py` pede ao juiz um JSON com nota 1-5 **por dimensão**
(`criteria_adherence`, `tone_and_persona`, `faithfulness` quando
aplicável) mais justificativa — nunca um score solto, pra que uma
regressão detectada aponte pra ONDE a qualidade caiu.

### Regressão: por que um arquivo local versionado, não só a UI

`eval/regression.py::compare_to_baseline` compara a média combinada da
run atual contra `eval/baseline.json` e falha o `asset_check` se a
queda passar do threshold (0.4, numa escala 1-5). A UI do LangFuse
mostra tendência pra inspeção humana; o `asset_check` precisa de algo
que possa **falhar sozinho**, então a baseline vive num arquivo
versionado em git (auditável via `git blame`/PR, igual qualquer outra
mudança de comportamento esperado). A baseline só AVANÇA — só é
sobrescrita quando a run atual não regrediu, pra não deslizar pra baixo
aos poucos através de várias pequenas quedas, cada uma dentro do
threshold.

**Verificado rodando o pipeline de ponta a ponta com as dependências
externas (LiteLLM Proxy/OpenRouter, LangFuse) mockadas**: uma run limpa
sem baseline anterior passa e grava a baseline; uma segunda run com
scores do juiz deliberadamente ruins (simulando um prompt degradado)
reprova o `asset_check` com a mensagem de regressão esperada, **e não**
sobrescreve a baseline — confirmando que a lógica de regressão
detecta e é resiliente a uma degradação real, não só teórica.

### Bugs reais encontrados rodando `make eval-run` de verdade

A primeira versão de `eval/assets.py::_task` era uma função **síncrona**
que chamava `asyncio.run(...)` internamente pra poder dar `await` em
`run_conversation` (que precisa de `Runner.run_async`). Rodando contra
o LiteLLM Proxy/LangFuse reais pela primeira vez, todos os 22 itens
falharam com `asyncio.run() cannot be called from a running event
loop`: `dataset.run_experiment(...)` já executa a task **dentro do
próprio event loop do SDK** (é assim que ele consegue paralelizar itens
via `max_concurrency`) — uma task síncrona que tenta abrir outro loop
por dentro colide com esse loop já rodando. A correção foi tornar
`_task` uma `async def` de verdade e deixar o SDK dar o `await` nela
diretamente, sem gerenciar o loop manualmente (o SDK aceita task
functions síncronas OU assíncronas, ver `eval/langfuse_client.py`).

Esse mesmo incidente expôs uma segunda lacuna: como todos os 22 itens
falharam na task, `result.item_results` não trazia nenhuma `Evaluation`
pra eles, e o código original simplesmente calculava a média sobre o
que sobrava — nesse caso, uma lista vazia (`StatisticsError`), mas com
uma falha PARCIAL (alguns itens falhando, outros não) o mesmo código
teria calculado uma média só sobre os itens que sobreviveram, sem
avisar que faltou gente. `eval_run` agora detecta itens sem os dois
scores esperados e falha alto, nomeando o(s) item(ns) afetado(s), em
vez de reportar uma média silenciosamente parcial — verificado
forçando uma falha sintética de task num item.

Rodando contra a OpenRouter de verdade (não mais mockada), uma terceira
descoberta: TODA chamada de TODO agente (não só do eval) falhava com
`402` — `"requested up to 65536 tokens, but can only afford..."`. A
OpenRouter pré-autoriza crédito com base no TETO de output do modelo
(65536 no Sonnet, mais ainda no Opus usado pelo `judge-model`), não no
uso real, e nenhum agente definia `max_tokens` explicitamente (ver
`app/agents/config/models.py::get_model_for_role`) — então toda
request implicitamente pedia esse teto inteiro, e uma conta sem saldo
pra cobrir o PIOR CASO era rejeitada antes de gerar uma palavra, mesmo
a resposta de verdade precisando de uma fração disso. Corrigido
definindo `max_tokens=2048` pra todos os agentes e `max_tokens=512`
pro `judge-model` (que só produz um JSON pequeno) — isso é uma
mudança fora do escopo original da Parte 6 (toca `app/agents/config/`,
usado por todas as partes), feita porque sem ela nenhuma chamada real
de LLM funcionava, em `eval-run` ou em qualquer outro fluxo.

Uma quarta descoberta, essa em `mcp_server/server.py` (Parte 4): um
item do golden set falhou com `AttributeError: 'RustBindingsAPI' object
has no attribute 'bindings'` seguido de `Could not connect to tenant
default_tenant`. Causa raiz — `eval/langfuse_client.py::run_experiment_sync`
roda vários itens do golden set em paralelo (`max_concurrency`), e mais
de um podia chamar `KnowledgeAgent` ao mesmo tempo através do MESMO
processo MCP (`McpToolset` é um singleton por processo, ver
`app/agents/knowledge.py`). O singleton lazy `_get_collection()` do
servidor MCP fazia só `if _collection is None:` sem trava nenhuma —
duas chamadas concorrentes podiam ver `None` ao mesmo tempo e as duas
tentarem construir um `chromadb.PersistentClient` sobre o MESMO
diretório simultaneamente, corrompendo a inicialização do binding Rust
de uma das duas. Antes da Parte 6, nada neste projeto chamava essa tool
de forma concorrente (uma conversa de cada vez em `test-live`/`adk
web`), então essa condição de corrida nunca tinha disparado. Corrigido
com double-checked locking (`threading.Lock`) em `_get_collection()` —
verificado com um teste real de concorrência
(`test_get_collection_singleton_init_is_thread_safe`, com 8 threads e
um `time.sleep` alargando a janela de corrida): a versão sem trava
reconstrói a coleção 8 de 8 vezes; com a trava, exatamente 1.

```bash
make eval-run   # roda o golden set completo -- custa chamadas reais de
                # LLM (uma conversa por item + uma chamada ao
                # judge-model por item), precisa do proxy E do LangFuse
                # de pé (make proxy-up feito automaticamente; make
                # langfuse-up precisa já estar rodando)
make eval-dev   # abre a UI do Dagster pra rodar o pipeline manualmente
```

Depois de um `make eval-run` bem-sucedido, confirme na UI do LangFuse
(`http://localhost:3000`): **Datasets** mostra `sdr-bot-golden-set` com
22 itens, e cada item tem uma **run** com os `Scores`
(`structural_pass`, `judge_criteria_adherence`, `judge_tone_and_persona`,
`judge_overall`, `judge_faithfulness` quando aplicável) anexados ao
trace correspondente. `eval/baseline.json` (gerado localmente) deveria
ser commitado depois da primeira run bem-sucedida, pra virar a
baseline conhecida do time.

### Rodando os testes da Parte 6 isoladamente

```bash
uv run pytest tests/test_eval_golden_set.py tests/test_eval_judge.py \
  tests/test_eval_regression.py tests/test_eval_runner.py -v
```

Camada 2 pura — formato do golden set, parsing do JSON do juiz, lógica
de comparação de regressão, `check_structural` com `session.state`
sintético. Nenhum desses custa LLM nem precisa do LangFuse/proxy
rodando, mesmo padrão rigoroso de `test_ingestion_chunking.py` (Parte 4).

## CI/CD (GitHub Actions) e deploy na GCP

> Migrado de GitLab CI para GitHub Actions — ver `git log -- .gitlab-ci.yml`
> (arquivo removido) pra a versão anterior. Motivo da migração: a cota
> gratuita de 400 minutos de CI/mês do GitLab estava perto do limite; o
> GitHub Actions dá uma cota gratuita bem maior pro mesmo uso.

### Modelo de branches

```
feature branches ──PR──► develop ──PR──► main
   (trabalho acontece)   (integração,      (só deploy — nada mais)
                          default branch
                          do repositório)
```

- **`develop`** é a branch padrão do repositório (Settings → General →
  Default branch). Toda feature branch abre PR contra ela.
  `lint`/`test`/`docker_build_check` rodam em qualquer PR contra `main` e
  em todo push — feedback rápido, sem tocar em nada de GCP.
- **`main`** só recebe merge vindo de `develop`, quando o conjunto de
  mudanças está pronto pra ir pro ar. É a **única** branch que os jobs
  `build_and_push`/`deploy_*` reconhecem (`if: github.ref ==
  'refs/heads/main' && github.event_name == 'push'` em
  `.github/workflows/ci-cd.yml`) — um push direto em `develop` nunca
  aciona deploy, só em `main`.
- Deliberadamente **não** é GitFlow completo (sem release/hotfix
  branches) — pra um projeto deste porte, esse processo extra não paga
  o custo de manutenção.
- Recomendado: proteger `main` em Settings → Branches → Branch protection
  rules (só merge via PR, sem push direto).

### Pipeline

O pipeline (`.github/workflows/ci-cd.yml`) é evolutivo, em duas camadas:

1. **Sempre roda, sem credencial nenhuma**: `lint`, `test` (smoke tests,
   sem chamada real de LLM), `docker_build_check` (valida que os
   Dockerfiles buildam) e `codeql` (SAST nativo do GitHub) — em qualquer
   PR e em todo push. Isso mantém o pipeline verde desde o primeiro
   commit, mesmo antes de qualquer configuração de nuvem.
2. **Só roda em push pra `main`**: `build_and_push` (Artifact Registry +
   Trivy container scanning) e os quatro `deploy_*` (Cloud Run) —
   condicionados só pela branch/evento, não por nenhuma variável de
   ambiente existir. Aprovação manual (equivalente ao `when: manual` do
   GitLab) é feita via GitHub Environments com "required reviewers"
   (Settings → Environments → `production-*`), não dá pra expressar isso
   só no YAML do workflow.

Arquitetura de deploy: três serviços Cloud Run — `litellm-proxy`
(gateway pra OpenRouter), `sdr-bot-api` (API FastAPI sobre o sistema de
agentes, aponta pro proxy via `LITELLM_PROXY_URL`), `telegram-service`
(webhook do Telegram, aponta pro `sdr-bot-api` via `SDR_BOT_API_URL`) —
mais uma VM (não Cloud Run) rodando Phoenix + LangFuse, ver "VM de
observabilidade" logo abaixo.

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
    --display-name="GitHub Actions CI/CD deployer"

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

# 4. Criar o Workload Identity Pool + Provider pro GitHub Actions
gcloud iam workload-identity-pools create github-pool \
    --location="global" --display-name="GitHub Actions CI"

gcloud iam workload-identity-pools providers create-oidc github-provider \
    --location="global" --workload-identity-pool="github-pool" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository == '<seu-usuario>/<seu-repo>'"

# 5. Permitir que a identidade federada do GitHub Actions impersone a service account
gcloud iam service-accounts add-iam-policy-binding \
    "gitlab-ci-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/<seu-usuario>/<seu-repo>"

# 6. Guardar os segredos de runtime no Secret Manager (não em GitHub Secrets)
echo -n "sua-chave-openrouter" | gcloud secrets create openrouter-api-key --data-file=-
echo -n "sua-master-key-do-proxy" | gcloud secrets create litellm-proxy-key --data-file=-
```

> Nota: a service account continua se chamando `gitlab-ci-deployer` —
> é só um `account_id`, renomeá-la força recriação do recurso (e
> re-sincronizar o secret `WIF_SERVICE_ACCOUNT` no GitHub em lockstep
> pra não quebrar o próximo deploy), então ficou como está deliberadamente
> em vez de renomear só por estética.

### Secrets a configurar no GitHub (Settings → Secrets and variables → Actions)

| Secret | Valor |
|---|---|
| `GCP_PROJECT_ID` | ID do projeto GCP |
| `GCP_PROJECT_NUMBER` | Número do projeto (`gcloud projects describe`) |
| `GCP_REGION` / `AR_REGION` | ex: `us-central1` |
| `AR_REPOSITORY` | `sdr-bot-repo` |
| `WIF_POOL_ID` | `github-pool` |
| `WIF_PROVIDER_ID` | `github-provider` |
| `WIF_SERVICE_ACCOUNT` | `gitlab-ci-deployer@<project-id>.iam.gserviceaccount.com` |
| `CLOUD_SQL_CONNECTION_NAME` | `PROJETO:REGIAO:INSTANCIA` (sai de `terraform output`) |
| `VPC_CONNECTOR_NAME` | sai de `terraform output` (ver "VM de observabilidade" abaixo) |
| `OBSERVABILITY_VM_INTERNAL_IP` | idem |

Nenhuma chave JSON de service account é armazenada em lugar nenhum — a
autenticação usa o ID token OIDC que o próprio GitHub Actions emite por
job (`id-token: write` em `.github/workflows/ci-cd.yml`, via
`google-github-actions/auth`), trocado por uma credencial federada de
curta duração.

### VM de observabilidade (Phoenix + LangFuse)

**Por que uma VM, não Cloud Run**: já tentamos Phoenix em Cloud Run
duas vezes, e as duas falharam por um motivo estrutural, não um bug
pontual. Cloud Run só aloca CPU pro container ENQUANTO ele processa uma
requisição:

- `batch=True` (BatchSpanProcessor, exporta em background a cada 5s):
  a thread de flush nunca é escalonada entre requisições — zero traces
  chegavam no Phoenix, sempre, confirmado checando os logs do próprio
  Cloud Run do Phoenix (zero requisições em `/v1/traces`).
- `batch=False` (SimpleSpanProcessor, exporta de forma síncrona,
  inline): os exports começaram a falhar por timeout em produção,
  bloqueando o `/chat` inteiro até esgotar as 3 instâncias — um
  incidente real, mitigado via rollback de tráfego pro revision
  anterior. Causa exata do timeout nunca confirmada, mas plausível que
  o PRÓPRIO Phoenix (também em Cloud Run, também sujeito a CPU
  throttling) estivesse lento o bastante pra estourar o timeout do
  exporter.

Uma VM normal, sempre ligada, não tem esse problema em nenhum dos dois
lados — nem quem exporta (ainda `sdr-bot-api`, ainda em Cloud Run, é
por isso que o código continua em `batch=True` por enquanto) nem quem
recebe (agora a VM, não mais Cloud Run).

**Infra** (`terraform/observability_vm.tf`): uma VPC dedicada, sem IP
externo, alcançável pelo Cloud Run só via Serverless VPC Access
(`--vpc-connector`/`--vpc-egress=private-ranges-only` nos jobs
`deploy_litellm_proxy`/`deploy_sdr_bot_api`) — nunca exposta na
internet pública, já que os traces contêm o conteúdo real da conversa
do lead. `observability/docker-compose.yml` (7 containers: os 6 do
LangFuse + Phoenix, com `PHOENIX_WORKING_DIR` num volume nomeado —
diferente do Cloud Run, aqui o storage persiste de verdade entre
restarts) é enviado pra um bucket GCS e baixado pela própria VM no
boot, junto com um `.env` puxado do Secret Manager (`gcloud secrets
versions access`).

**Segredos a popular manualmente** (mesma regra dos outros — nunca via
Terraform):

```bash
# Blob único com todas as variáveis LANGFUSE_* (ver .env.example) --
# mesmo conteúdo que `make langfuse-secrets` já gera hoje pro .env local.
cat <<'EOF' | gcloud secrets versions add observability-vm-env --data-file=-
LANGFUSE_SALT=...
LANGFUSE_ENCRYPTION_KEY=...
LANGFUSE_NEXTAUTH_SECRET=...
LANGFUSE_POSTGRES_PASSWORD=...
LANGFUSE_CLICKHOUSE_PASSWORD=...
LANGFUSE_REDIS_AUTH=...
LANGFUSE_MINIO_ROOT_PASSWORD=...
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_INIT_USER_PASSWORD=...
EOF

# MESMOS valores de LANGFUSE_PUBLIC_KEY/SECRET_KEY acima, como secrets
# individuais -- litellm-proxy (Cloud Run) referencia estes dois via
# --set-secrets, não o blob acima.
echo -n "..." | gcloud secrets versions add langfuse-public-key --data-file=-
echo -n "..." | gcloud secrets versions add langfuse-secret-key --data-file=-
```

**Verificação pós-deploy** (a VM não tem IP público, então acesso é
via IAP — exige `roles/iap.tunnelResourceAccessor` na sua identidade):

```bash
gcloud compute ssh observability-vm --zone=us-central1-a --tunnel-through-iap \
  -- 'docker compose -f /opt/observability/docker-compose.yml ps'

# Túnel local pra abrir a UI do LangFuse (http://localhost:3000) ou do
# Phoenix (http://localhost:6006) no seu navegador:
gcloud compute ssh observability-vm --zone=us-central1-a --tunnel-through-iap \
  -- -L 3000:localhost:3000 -L 6006:localhost:6006
```

**Antes de trocar `batch=True` → `batch=False`** em
`app/agents/observability.py` de novo: confirme que a VM responde
rápido e de forma estável com um teste direto primeiro (`curl` num
loop, várias vezes, sob alguma carga), não só uma vez — foi
exatamente uma condição transiente que passou despercebida da última
vez que isso foi tentado em Cloud Run.

### Deploy via Terraform

O setup manual acima (`gcloud` passo a passo) funciona, mas não é
idempotente nem versionado — rodar os mesmos comandos duas vezes por
engano, ou esquecer um passo num projeto GCP novo, é o tipo de drift que só
aparece três meses depois, num incidente. `terraform/` provisiona a mesma
infraestrutura (e mais: Cloud SQL e os secrets do `telegram_service`, que o
script manual nunca cobriu) de forma declarativa — o script acima vira,
efetivamente, "o que o Terraform faz por baixo dos panos", não mais o
procedimento a seguir manualmente.

**Divisão de responsabilidade**: o Terraform provisiona a infraestrutura
fundacional (APIs, Artifact Registry, WIF, secrets vazios, Cloud SQL, IAM).
O pipeline de CI continua fazendo o deploy em si (`gcloud run deploy`, já
idempotente — cria ou atualiza a revisão). Os três serviços Cloud Run
(`litellm-proxy`, `sdr-bot-api`, `telegram-service`) **não** são recursos
Terraform — recriá-los ali geraria conflito de posse com os deploys
imperativos do CI.

#### Pré-requisitos

- Projeto GCP com billing ativo (crie um novo se for só testar isto —
  ver aviso de custo abaixo antes de deixar rodando).
- `gcloud auth application-default login` (Terraform usa essas
  credenciais para provisionar).
- Permissão de Owner/Editor no projeto (é operação de bootstrap, feita
  uma vez só, por isso não vale a pena desenhar um papel mais restrito
  só para isto).

#### Estrutura de `terraform/`

```
terraform/
├── main.tf               # provider, APIs habilitadas
├── artifact_registry.tf  # repositório Docker
├── secrets.tf            # openrouter-api-key, litellm-proxy-key, session-db-url (recursos vazios)
├── workload_identity.tf  # pool + provider OIDC do GitHub Actions, SA de deploy, bindings
├── cloud_sql.tf          # instância Postgres + SA de runtime do sdr-bot-api
├── telegram.tf           # telegram-bot-token, telegram-webhook-secret
├── variables.tf
├── outputs.tf             # os 9 valores que viram CI/CD variables
└── terraform.tfvars.example
```

#### 1. Configurar variáveis e aplicar

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# preencha gcp_project_id, gcp_project_number e github_repository
# (terraform.tfvars não deve ser commitado -- já está no .gitignore)

terraform init
terraform apply
```

#### 2. Popular os 5 secrets manualmente (nunca via Terraform)

Os recursos `google_secret_manager_secret` ficam vazios de propósito — os
valores reais nunca entram em `.tf` nem no state:

```bash
echo -n "sua-chave-openrouter"      | gcloud secrets versions add openrouter-api-key     --data-file=-
echo -n "sua-master-key-do-proxy"   | gcloud secrets versions add litellm-proxy-key       --data-file=-
echo -n "token-do-bot-do-telegram"  | gcloud secrets versions add telegram-bot-token       --data-file=-
echo -n "segredo-do-webhook"        | gcloud secrets versions add telegram-webhook-secret --data-file=-
```

#### 3. Cloud SQL: criar usuário e popular `session-db-url`

O Terraform cria a instância e o banco (`sdr_bot`), mas **não** o
usuário/senha — isso é criado à mão pelo mesmo motivo dos outros secrets
(nenhuma credencial no state):

```bash
gcloud sql users create sdr_bot_app --instance=sdr-bot-sessions-db --password="<senha-gerada>"

CONN=$(terraform output -raw CLOUD_SQL_CONNECTION_NAME)
echo -n "postgresql+asyncpg://sdr_bot_app:<senha-gerada>@/sdr_bot?host=/cloudsql/${CONN}" \
  | gcloud secrets versions add session-db-url --data-file=-
```

> **Aviso de custo**: dos quatro recursos com custo fixo deste stack
> (Artifact Registry, Secret Manager, Cloud Run e Cloud SQL), o **Cloud
> SQL é de longe o mais caro** — mesmo o tier mais barato (`db-f1-micro`)
> fica em torno de US$9-10/mês só de instância, mais ~US$1,70/mês de 10GB
> de disco, cobrado o mês inteiro **mesmo sem nenhum tráfego** (Cloud Run,
> em contraste, só cobra por uso real). Para um projeto de portfólio que
> não fica no ar o tempo todo, considere `terraform destroy
> -target=google_sql_database_instance.sessions_db` entre demonstrações, e
> recriar (+ recriar o usuário e repopular `session-db-url`) quando for
> mostrar de novo.

#### 4. Copiar os outputs para os Secrets do GitHub

```bash
terraform output
```

Cole cada um dos valores em Settings → Secrets and variables → Actions
(tabela acima, já atualizada com `CLOUD_SQL_CONNECTION_NAME`).

#### 5. Rodar o pipeline

Push (ou merge de PR) para `main` já dispara o pipeline inteiro
automaticamente — os jobs `deploy_phoenix` → `deploy_litellm_proxy` →
`deploy_sdr_bot_api` → `deploy_telegram_service` rodam em sequência (os
dois primeiros em paralelo). Se algum `production-*` Environment tiver
"required reviewers" configurado (Settings → Environments), o respectivo
job fica pendente de aprovação manual em vez de rodar direto.

#### 6. Verificação pós-deploy

```bash
API_URL=$(gcloud run services describe sdr-bot-api      --region="$GCP_REGION" --format='value(status.url)')
TG_URL=$(gcloud run services describe telegram-service   --region="$GCP_REGION" --format='value(status.url)')
curl -sf "$API_URL/health"
curl -sf "$TG_URL/health"

# Persistência de sessão: mesma session_id, duas chamadas sequenciais --
# prova de que o Cloud SQL está de fato conectado, não só provisionado.
curl -s -X POST "$API_URL/chat" -H 'content-type: application/json' \
  -d '{"user_id":"test:1","session_id":"sess-A","message":"meu nome é Danilo","channel":"web"}'
curl -s -X POST "$API_URL/chat" -H 'content-type: application/json' \
  -d '{"user_id":"test:1","session_id":"sess-A","message":"qual é o meu nome?","channel":"web"}'
# esperado: a segunda resposta referencia "Danilo"

# Telegram real: registrar o webhook apontando pra URL pública do Cloud Run
curl -s "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=${TG_URL}/webhook" -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
# enviar uma mensagem real pelo Telegram, depois conferir os logs:
gcloud run services logs read telegram-service --region="$GCP_REGION" --limit=50
gcloud run services logs read sdr-bot-api       --region="$GCP_REGION" --limit=50
```

#### Gaps conhecidos, documentados e não resolvidos aqui

- ~~**RAG em produção**~~ -- RESOLVIDO. O `Dockerfile` raiz materializa
  o índice Chroma **durante o build da imagem** (roda `dagster asset
  materialize -f ingestion/definitions.py --select '*'` no builder
  stage, copia `mcp_server/` -- server.py + o `chroma_data/` recém-
  gerado -- pro runtime stage), em vez de GCS + sync em runtime: zero
  recursos Terraform novos, trade-off aceito de rebuild a cada mudança
  na base de conhecimento (`ingestion/knowledge_base/*.md`).
  Verificado rodando o build de verdade e consultando a coleção
  resultante (recall correto pra "quanto custa o plano"). Dois bugs
  reais encontrados fazendo isso:
  - `knowledge.py` chamava o servidor MCP via `command="uv", args=
    ["run", "python", ...]` -- o runtime stage nunca teve `uv`
    instalado (só o builder), então o subprocess falhava com
    `[Errno 2] No such file or directory` na primeira pergunta de RAG.
    Trocado por `command=sys.executable` (o interpretador do processo
    atual, que já é o `.venv/bin/python` certo nos dois ambientes).
  - O `asset_check` `no_pii_leaked_into_index` (que faz `from
    app.agents.pii.engine import ...`) falhava com `ModuleNotFoundError:
    No module named 'app'` DENTRO do build -- o executor multiprocess
    do Dagster lança um subprocess novo por step, e esse step
    específico não herdava `/app` no `sys.path`. Precisou de
    `PYTHONPATH=/app` explícito só nesse `RUN`.
  Nota operacional: no GitHub Actions (runners efêmeros, sem cache de
  camada Docker entre execuções), esse stage roda em TODO push pra
  `main`, não só quando a base de conhecimento muda -- baixa de novo o
  modelo de embedding (~470MB) a cada deploy. Aceito por enquanto;
  adicionar cache de build (ex: `docker/build-push-action` com cache
  no GHA ou num registry) resolveria, mas fica como otimização futura,
  não bloqueador.
- **Hardening do `--allow-unauthenticated`** (`litellm-proxy` e
  `telegram-service`): TODO consciente, junto dos guardrails da Parte 3.
