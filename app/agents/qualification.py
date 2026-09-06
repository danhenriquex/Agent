"""
Qualification Agent — conduz a descoberta inicial (framework BANT) para
decidir se o lead deveria avançar no funil comercial.

Parte 3: ganhou a tool set_qualification_status, que é a forma
estruturada e auditável de declarar "esse lead está qualificado" —
antes disso, só existia texto livre em qualification_notes, o que não
dava pra checagem confiável em outro lugar do sistema (ex: o
before_tool_callback de scheduling.py que agora depende desse status).

Padrão "booking-first" original (o mesmo que Drift/Qualified/Chili Piper
usam): o objetivo não era completar um checklist BANT inteiro antes de
sugerir agendar -- era chegar a um convite pra marcar uma conversa o
quanto antes. Na prática (visto em teste manual real, session.db) isso
saía forte demais: um único "tenho dificuldade em gerenciar o ponto com
planilhas" já bastava pro agente convidar pra reunião, sem nunca
explicar qual produto resolve aquela dor nem aprofundar o contexto --
o lead recebia uma oferta de agendamento antes de qualquer conversa de
verdade acontecer.

Correção: este agente não convida mais pra reunião -- ele só extrai
contexto (dor, tamanho da empresa, o que o lead usa hoje) e registra
isso via set_qualification_status. É o Orchestrator (orchestrator.py)
quem decide o que fazer com isso -- explicar o produto que resolve a
dor via KnowledgeAgent, aprofundar mais, ou sugerir agendamento -- e só
chega em "sugerir agendamento" quando o lead pede explicitamente ou a
conversa para de avançar. "in_progress" continua um status
perfeitamente aceitável enquanto a dor ainda não está clara.

set_qualification_status agora também captura um "opportunity brief"
estruturado (pain, product_of_interest, company_size, additional_notes)
em vez de só status + reasoning em texto livre -- é o mesmo problema que
motivou a tool em si: sem campos estruturados, o SchedulingAgent (ou um
futuro Closer/CRM) só teria acesso a qualification_notes como blob de
texto, sem conseguir extrair "qual é a dor" ou "em qual produto ele tem
interesse" de forma confiável. STATE_QUALIFICATION_STATUS continua uma
string plana de propósito -- é o contrato que enforce_action_allowlist
já depende de checar por igualdade.
"""

from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext
from pydantic import BaseModel

from .config.models import get_model_for_role
from .guardrails.model_error_recovery import recover_from_duplicated_tool_call_json
from .guardrails.output_policy import validate_output_policy
from .guardrails.prompt_injection import detect_prompt_injection
from .guardrails.transfer import block_unauthorized_transfer
from .persona import PERSONA_INTRO
from .pii.masking import mask_pii
from .session.state_schema import STATE_QUALIFICATION_NOTES, STATE_QUALIFICATION_STATUS


class QualificationDetails(BaseModel):
    """Resumo estruturado da qualificação -- o "opportunity brief" que
    fica em session.state[STATE_QUALIFICATION_NOTES] pra dar a quem
    assume a conversa depois (SchedulingAgent hoje; um Closer/CRM real
    amanhã) contexto imediato, sem precisar reler o chat inteiro."""

    status: Literal["qualified", "disqualified", "in_progress"]
    pain: str
    product_of_interest: str
    reasoning: str
    company_size: str | None = None
    additional_notes: str | None = None


def set_qualification_status(
    status: str,
    pain: str,
    product_of_interest: str,
    reasoning: str,
    tool_context: ToolContext,
    company_size: str | None = None,
    additional_notes: str | None = None,
) -> dict:
    """Registra o status de qualificação do lead e um resumo estruturado
    da oportunidade, com base na conversa até agora.

    Chame isso assim que tiver informação suficiente pra decidir —
    não precisa esperar cobrir todos os critérios BANT se já ficou
    claro que o lead é ou não um bom fit. Preencha pain e
    product_of_interest com o que já foi dito, mesmo que parcial --
    "não mencionado ainda" é uma resposta válida se o status for
    "in_progress".

    Args:
        status: um de "qualified", "disqualified", "in_progress".
        pain: o problema/necessidade que o lead quer resolver, na
            própria descrição dele (ex: "onboarding manual tomando
            muito tempo do time de CS").
        product_of_interest: qual produto, funcionalidade ou área da
            solução o lead demonstrou interesse (ex: "automação de
            onboarding", "módulo de relatórios").
        reasoning: justificativa breve (1-2 frases) pra essa decisão,
            citando o que da conversa embasou isso.
        company_size: porte da empresa, se mencionado (ex: "~50
            funcionários"). Opcional.
        additional_notes: qualquer outro sinal relevante pra quem for
            fechar a conversa depois e que não caiba nos campos acima
            (orçamento citado, prazo/urgência, concorrente mencionado,
            etc). Opcional.

    Returns:
        dict: confirmação do status e do opportunity_brief registrado.
    """
    valid_statuses = {"qualified", "disqualified", "in_progress"}
    if status not in valid_statuses:
        return {
            "status": "error",
            "error_message": f"status inválido: {status!r}. Use um de {valid_statuses}.",
        }

    details = QualificationDetails(
        status=status,
        pain=pain,
        product_of_interest=product_of_interest,
        reasoning=reasoning,
        company_size=company_size,
        additional_notes=additional_notes,
    )

    tool_context.state[STATE_QUALIFICATION_STATUS] = status
    tool_context.state[STATE_QUALIFICATION_NOTES] = details.model_dump()
    return {
        "status": "success",
        "recorded_status": status,
        "opportunity_brief": details.model_dump(),
    }


qualification_agent = LlmAgent(
    name="QualificationAgent",
    model=get_model_for_role("qualification"),
    description=(
        "Conduz a qualificação inicial do lead usando perguntas de "
        "descoberta (orçamento, autoridade de decisão, necessidade, "
        "prazo). Use quando o lead está no início da conversa ou quando "
        "ainda não sabemos se ele é um bom fit."
    ),
    instruction=(
        PERSONA_INTRO
        + "Você conduz a qualificação inicial de um lead B2B, mas de "
        "forma LEVE e RÁPIDA -- o objetivo é entender a dor do lead sem "
        "fazer um interrogatório longo. Um SDR de verdade não dispara "
        "várias perguntas seguidas antes de deixar a conversa avançar.\n\n"
        "Faça no máximo 1-2 perguntas curtas e naturais sobre o desafio "
        "do lead (o que ele quer resolver) e, se render, o tamanho da "
        "empresa e o que ele usa hoje pra isso. Intercale com contexto "
        "útil sobre como ajudamos empresas parecidas -- não faça um "
        "interrogatório.\n\n"
        "'in_progress' é um status perfeitamente aceitável: é normal "
        "deixar o resto da qualificação pra depois em vez de segurar o "
        "lead no chat. Assim que tiver QUALQUER sinal razoável de fit "
        "-- não precisa ser orçamento, autoridade e prazo completos -- "
        "chame set_qualification_status com o status apropriado. "
        "Preencha pain e product_of_interest com o que já foi dito na "
        "conversa (mesmo que parcial) e use additional_notes pra "
        "qualquer outro sinal útil pra quem for fechar depois (prazo, "
        "orçamento mencionado, concorrente citado) -- isso vira o "
        "resumo que a próxima etapa recebe, então não deixe informação "
        "relevante só na conversa em texto livre.\n\n"
        "Convidar o lead pra uma reunião NÃO é seu papel -- isso é "
        "decisão do Orchestrator, que primeiro explica qual produto "
        "resolve a dor do lead antes de considerar agendamento. Se o "
        "lead perguntar algo sobre o produto/preço que você não sabe, "
        "diga que vai verificar (o Orchestrator vai rotear para o "
        "agente certo)."
    ),
    tools=[set_qualification_status],
    # Chamado via AgentTool a partir do Orchestrator (ver orchestrator.py),
    # não via sub_agents — então este agente nunca ganha a ferramenta
    # transfer_to_agent para começar; não há transferência a bloquear. O
    # guardrail de transfer abaixo fica como defesa em profundidade.
    before_model_callback=[mask_pii, detect_prompt_injection],
    after_model_callback=[validate_output_policy, block_unauthorized_transfer],
    on_model_error_callback=recover_from_duplicated_tool_call_json,
)
