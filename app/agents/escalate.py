"""
Escalate to Human Agent — saída explícita do sistema.

Todo sistema de produção regulado precisa de um caminho claro para "eu não
devo responder isso sozinho". Ele existe como agente próprio (não como um
fallback silencioso dentro de outro agente) para que:

  (a) apareça no tracing (LangFuse/Phoenix, Parte 5) como um evento
      nomeado e auditável, e
  (b) o eval set (Parte 6) consiga medir separadamente "taxa de
      escalonamento correto" vs. "taxa de escalonamento perdido"
      (deveria ter escalado e não escalou).
"""

from google.adk.agents import LlmAgent

from app.agents._guardrails import block_unauthorized_transfer
from app.config.models import get_model_for_role
from app.session.state_schema import STATE_ESCALATED

escalate_agent = LlmAgent(
    name="EscalateToHumanAgent",
    model=get_model_for_role("escalate"),
    description=(
        "Encerra o atendimento automatizado e transfere para um humano. "
        "Use quando: o guardrail bloquear uma mensagem (Parte 3), o lead "
        "pedir explicitamente para falar com uma pessoa, ou a conversa "
        "sair do escopo comercial (suporte técnico, reclamação, assunto "
        "não relacionado a vendas)."
    ),
    instruction=(
        "Informe de forma clara e cordial que você vai conectar o lead "
        "com um especialista humano, e que alguém do time vai continuar "
        "a conversa em breve. Não tente resolver o pedido original você "
        "mesmo."
    ),
    output_key=STATE_ESCALATED,
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail abaixo fica como defesa em profundidade, não a proteção
    # primária (ver docstring de _guardrails.py para o histórico do bug).
    after_model_callback=block_unauthorized_transfer,
)
