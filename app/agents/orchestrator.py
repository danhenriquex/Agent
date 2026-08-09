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
"""

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from .config.models import get_model_for_role
from .escalate import escalate_agent
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .knowledge import knowledge_agent
from .objection import objection_agent
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
        "Você é o orquestrador de um time de vendas (SDR) automatizado. "
        "Para a maioria das mensagens, você deve CONSULTAR o especialista "
        "certo (chamando-o como ferramenta) antes de responder — não "
        "responda de memória sobre produto, preço, objeções ou "
        "agendamento.\n\n"
        "Regras de consulta:\n"
        "1) Se é o início da conversa ou ainda não sabemos se o lead é "
        "qualificado, consulte QualificationAgent.\n"
        "2) Se o lead pergunta sobre produto, funcionalidade ou preço, "
        "consulte KnowledgeAgent.\n"
        "3) Se o lead expressa hesitação, recusa ou objeção, consulte "
        "ObjectionHandlingAgent.\n"
        "4) Se o lead concorda em avançar / quer marcar uma conversa, "
        "consulte SchedulingAgent.\n"
        "5) Se o pedido está fora do escopo comercial, ou o lead pede "
        "explicitamente um humano, consulte EscalateToHumanAgent.\n\n"
        "Depois de consultar o especialista, entregue a resposta dele ao "
        "lead de forma natural (pode repassar quase literalmente — não "
        "precisa reescrever tudo). Só responda diretamente, sem consultar "
        "ninguém, para saudações simples."
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
)
