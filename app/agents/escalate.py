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

from .config.models import get_model_for_role
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .guardrails.transfer import block_unauthorized_transfer
from .pii.masking import mask_pii
from .session.state_schema import STATE_ESCALATED

escalate_agent = LlmAgent(
    name="EscalateToHumanAgent",
    model=get_model_for_role("escalate"),
    description=(
        "Encerra o atendimento automatizado e transfere para um humano. "
        "Use quando: o guardrail bloquear uma mensagem, o lead pedir "
        "explicitamente para falar com uma pessoa, ou a conversa sair do "
        "escopo comercial (suporte técnico, reclamação, assunto não "
        "relacionado a vendas)."
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
    # guardrail de transfer abaixo fica como defesa em profundidade.
    before_model_callback=[mask_pii, detect_prompt_injection],
    after_model_callback=[validate_output_policy, block_unauthorized_transfer],
)
