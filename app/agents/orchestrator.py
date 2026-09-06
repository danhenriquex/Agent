"""
Orchestrator Agent — raiz da hierarquia. Consulta os agentes
especialistas como TOOLS (via AgentTool), não via sub_agents/
transfer_to_agent.

Por que essa mudança (Parte 1, revisão pós-debug manual): o desenho
original usava sub_agents, que no ADK significa "transferência
permanente de controle" — o especialista assume a conversa e o
Orchestrator sai do circuito. Isso nunca foi o comportamento que
queríamos (o objetivo sempre foi "o Orchestrator consulta um
especialista e decide a resposta final", não "o especialista assume
para sempre") — e na prática causou dois bugs reais em teste manual:

1. Um crash de JSON malformado ao processar uma chamada de
   transfer_to_agent (google/adk-python#1038 — argumentos de tool call
   concatenados/duplicados).
2. Uma tentativa de transferência não autorizada entre agentes irmãos,
   mesmo com disallow_transfer_to_peers=True — porque essa flag só edita
   o texto do prompt, não remove a ferramenta de fato
   (google/adk-python#3850, ainda aberto).

AgentTool não tem esse mecanismo: os agentes especialistas nunca ganham
a ferramenta transfer_to_agent, porque não fazem parte da árvore de
sub_agents — o Orchestrator os chama como função, recebe o resultado, e
permanece no controle da conversa. Isso elimina a categoria inteira do
bug, em vez de tentar bloqueá-lo depois que já aconteceu.

Trade-off conhecido: o autor do evento de resposta final agora é sempre
"OrchestratorAgent" (ele reprocessa o resultado do especialista antes de
responder ao usuário) — o debug via CLI ("[NomeDoAgente] ...") perde a
visibilidade direta de qual especialista respondeu uma pergunta
específica. Aceitável por ora; pode ser recuperado depois inspecionando
eventos intermediários, se necessário.

Documentação: https://google.github.io/adk-docs/agents/multi-agents/#agents-as-tools

Parte 2 — mascaramento de PII: este agente é o único ponto de entrada
(recebe a mensagem crua do usuário) e o único ponto de saída (entrega a
resposta final) de todo o sistema. Por isso ele é o único que registra
unmask_pii — reverter tokens em qualquer outro lugar arriscaria vazar
PII de volta pro contexto antes da resposta final estar pronta. Todos
os agentes (incluindo este) registram mask_pii, para que nenhum deles
jamais veja PII crua, mesmo que uma ferramenta futura (Parte 4+) traga
dado bruto de algum lugar. Ver app/agents/pii/masking.py.

Parte 3 — guardrails: mesmo padrão de defesa em profundidade.
detect_prompt_injection e validate_output_policy rodam em TODO agente
(incluindo este); enforce_action_allowlist (before_tool_callback) só
existe em SchedulingAgent, porque é a única tool com uma ação que
precisa de allowlist hoje. Ver app/agents/guardrails/.

Bug real observado em teste manual (session.db): QualificationAgent
pergunta o prazo e deixa qualification_status como "in_progress",
dizendo (na própria resposta) que vai chamar set_qualification_status
de novo assim que tiver a resposta. O lead responde à pergunta -- mas a
regra 4 (abaixo) mandava rotear direto pra SchedulingAgent nesse ponto,
sem nunca voltar pro QualificationAgent pra fechar a decisão. Resultado:
qualification_status ficava travado em "in_progress" pra sempre, e se o
lead escolhesse um horário, enforce_action_allowlist bloquearia
book_meeting com o link de "complete seu perfil" -- mesmo o lead tendo
acabado de responder exatamente o que foi perguntado. A regra 1 agora
cobre esse caso explicitamente: resposta a uma pergunta de qualificação
em aberto volta pro QualificationAgent antes de ir pro Scheduling.

Segundo bug real observado (mesma fonte, teste manual): a regra antiga
"lead concorda em avançar -> consulte SchedulingAgent, mesmo sem
qualificação completa" era liberal demais na prática -- o modelo
interpretava uma simples descrição de dor ("tenho dificuldade em
gerenciar o ponto com planilhas") como sinal suficiente pra já buscar
horários, tudo numa única resposta, sem nunca explicar qual produto
resolve aquela dor nem aprofundar o contexto. O lead recebia uma oferta
de reunião antes de qualquer conversa de vendas de verdade acontecer.
As regras abaixo agora inserem uma etapa de "explicar o produto que
resolve a dor" (via KnowledgeAgent, com um pedido direcionado à dor
relatada -- não uma lista genérica de planos) antes de considerar
agendamento, e restringem SchedulingAgent a dois casos: pedido
explícito do lead, ou a conversa parar de avançar depois que a dor e o
produto já foram discutidos.
"""

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from .config.models import get_model_for_role
from .escalate import escalate_agent
from .guardrails.model_error_recovery import recover_from_duplicated_tool_call_json
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .knowledge import knowledge_agent
from .objection import objection_agent
from .persona import COMPANY_NAME, PERSONA_INTRO
from .pii.masking import mask_pii, unmask_pii
from .qualification import qualification_agent
from .scheduling import scheduling_agent

root_agent = LlmAgent(
    name="OrchestratorAgent",
    model=get_model_for_role("orchestrator"),
    description=(
        "Coordenador do atendimento SDR. Recebe toda mensagem do lead e "
        "decide qual especialista consultar antes de responder."
    ),
    instruction=(
        PERSONA_INTRO
        + "Você é o orquestrador de um time de vendas (SDR) automatizado. "
        "Para a maioria das mensagens, você deve CONSULTAR o especialista "
        "certo (chamando-o como ferramenta) antes de responder — não "
        "responda de memória sobre produto, preço, objeções ou "
        "agendamento.\n\n"
        "Para uma saudação simples (ex: 'oi', 'olá', 'bom dia'), responda "
        "você mesmo, sem consultar ninguém, seguindo este padrão -- "
        "direto ao ponto, nunca um 'como posso ajudar?' genérico e vazio:\n"
        f"1) Se apresente em uma frase: seu nome, {COMPANY_NAME}, e o que "
        "a empresa faz.\n"
        "2) Ofereça dois caminhos ao mesmo tempo, na mesma mensagem: uma "
        "pergunta leve sobre o desafio do lead, OU a opção de já ver "
        "horários pra uma conversa rápida com o time. Deixe explícito que "
        "agendar é uma opção disponível desde já -- não algo que só "
        "aparece depois de uma qualificação longa.\n\n"
        "Regras de consulta pro resto da conversa:\n"
        "1) Se ainda não sabemos a dor do lead, consulte "
        "QualificationAgent. Isso inclui o caso em que a última consulta "
        "deixou qualification_status como 'in_progress' e a mensagem "
        "atual do lead responde à pergunta que ficou em aberto -- volte "
        "pro QualificationAgent com essa resposta ANTES de considerar "
        "qualquer outra regra, mesmo que a resposta também pareça "
        "sinalizar interesse em agendar. Só assim a qualificação é "
        "fechada (qualified/disqualified) em vez de ficar presa em "
        "'in_progress' pra sempre.\n"
        "2) Assim que a dor do lead estiver clara (mesmo que "
        "qualification_status ainda seja 'in_progress') e você ainda não "
        "tiver explicado qual produto/plano da Helssing resolve ESSA dor "
        "específica nesta conversa, consulte KnowledgeAgent pedindo uma "
        "recomendação direcionada à dor relatada -- não uma lista "
        "genérica de todos os planos. Isso vem ANTES de considerar a "
        "regra 5: o objetivo é desenvolver a conversa e mostrar fit de "
        "produto, não coletar o mínimo pra já empurrar uma reunião.\n"
        "3) Se o lead pergunta algo específico sobre produto, "
        "funcionalidade ou preço (mesmo depois da regra 2 já ter "
        "rodado), consulte KnowledgeAgent de novo.\n"
        "4) Se o lead expressa hesitação, recusa ou objeção, consulte "
        "ObjectionHandlingAgent.\n"
        "5) Só consulte SchedulingAgent quando (a) o lead pede "
        "explicitamente pra marcar/agendar, OU (b) a dor e o produto que "
        "resolve ela já foram explicados nesta conversa (regra 2) e a "
        "conversa parou de avançar -- o lead só confirmou/concordou sem "
        "trazer pergunta ou informação nova. Fora desses dois casos, não "
        "busque horários nem termine a resposta oferecendo uma reunião "
        "por padrão -- continue aprofundando a conversa (regras 1-4) "
        "antes disso. Quando a regra 5 valer, é papel do SchedulingAgent "
        "(e do guardrail de allowlist) decidir se já pode confirmar ou "
        "se precisa voltar pra qualificação primeiro; você não precisa "
        "bloquear isso aqui.\n"
        "6) Se o pedido está fora do escopo comercial, ou o lead pede "
        "explicitamente um humano, consulte EscalateToHumanAgent.\n\n"
        "Depois de consultar o especialista, entregue a resposta dele ao "
        "lead de forma natural (pode repassar quase literalmente — não "
        "precisa reescrever tudo)."
    ),
    tools=[
        AgentTool(agent=qualification_agent),
        AgentTool(agent=knowledge_agent),
        AgentTool(agent=objection_agent),
        AgentTool(agent=scheduling_agent),
        AgentTool(agent=escalate_agent),
    ],
    before_model_callback=[mask_pii, detect_prompt_injection],
    after_model_callback=[validate_output_policy, unmask_pii],
    on_model_error_callback=recover_from_duplicated_tool_call_json,
)
