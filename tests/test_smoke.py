"""
Smoke test — valida a topologia do sistema multi-agente SEM fazer nenhuma
chamada real de API. Isso é intencional: esse teste roda em CI sem
precisar de OPENROUTER_API_KEY, e existe só pra travar erros estruturais
(ex: alguém troca um AgentTool pelo agente errado, ou description fica
vazia).

Nota de arquitetura: o Orchestrator consulta os especialistas via
AgentTool (root_agent.tools), não via sub_agents/transfer_to_agent — ver
docstring de app/agents/orchestrator.py para o porquê dessa escolha.

A Parte 6 vai adicionar testes de comportamento de verdade (eval set +
LLM-as-judge), que aí sim fazem chamadas reais e custam dinheiro/tempo —
por isso ficam separados deste smoke test.
"""

from google.adk.tools.agent_tool import AgentTool

from app.agents import root_agent

_EXPECTED_SPECIALIST_NAMES = {
    "QualificationAgent",
    "KnowledgeAgent",
    "ObjectionHandlingAgent",
    "SchedulingAgent",
    "EscalateToHumanAgent",
}


def _specialist_agents():
    """Extrai os agentes especialistas de dentro dos AgentTool do Orchestrator."""
    return [tool.agent for tool in root_agent.tools if isinstance(tool, AgentTool)]


def test_orchestrator_has_no_sub_agents():
    # Confirma a escolha de arquitetura: o Orchestrator não usa
    # sub_agents (transferência permanente), só AgentTool (consulta
    # pontual). Um sub_agent aparecendo aqui seria sinal de regressão
    # para o padrão antigo que causou os bugs de transfer_to_agent.
    assert root_agent.sub_agents == []


def test_orchestrator_consults_all_specialists_via_agent_tool():
    actual_names = {agent.name for agent in _specialist_agents()}
    assert actual_names == _EXPECTED_SPECIALIST_NAMES


def test_every_specialist_has_a_routable_description():
    # Sem description, o Orchestrator não tem base pra decidir qual
    # AgentTool chamar — isso quebra o sistema silenciosamente.
    for agent in _specialist_agents():
        assert agent.description and len(agent.description.strip()) > 20, (
            f"{agent.name} tem description ausente ou curta demais para "
            "roteamento confiável"
        )


def test_specialists_have_no_parent_agent():
    # Confirma que os especialistas NÃO fazem parte de uma árvore de
    # sub_agents — é isso que garante que eles nunca ganham a ferramenta
    # transfer_to_agent para começar (a causa raiz dos dois bugs que já
    # depuramos manualmente: google/adk-python#1038 e #3850).
    for agent in _specialist_agents():
        assert agent.parent_agent is None, (
            f"{agent.name} tem parent_agent definido — isso reintroduziria "
            "a ferramenta transfer_to_agent e os bugs associados a ela"
        )


def test_every_agent_instruction_includes_company_name():
    # Barato e sem chamada de LLM -- pega o caso "criei um agente novo e
    # esqueci de importar PERSONA_INTRO", antes de precisar de um teste
    # ao vivo pra descobrir isso.
    from app.agents.persona import COMPANY_NAME

    all_agents = [root_agent, *_specialist_agents()]
    for agent in all_agents:
        assert COMPANY_NAME in agent.instruction, (
            f"{agent.name} não tem {COMPANY_NAME} na instruction -- "
            "provavelmente esqueceu de usar PERSONA_INTRO"
        )


def test_every_agent_recovers_from_duplicated_tool_call_json():
    # BerriAI/litellm#20543: modelos Claude ocasionalmente emitem
    # argumentos de tool call como JSON duplicado. Defesa em
    # profundidade -- qualquer agente com tools pode ser afetado, não
    # só onde foi observado a primeira vez (QualificationAgent).
    from app.agents.guardrails.model_error_recovery import (
        recover_from_duplicated_tool_call_json,
    )

    all_agents = [root_agent, *_specialist_agents()]
    for agent in all_agents:
        assert agent.on_model_error_callback is recover_from_duplicated_tool_call_json, (
            f"{agent.name} não tem recover_from_duplicated_tool_call_json registrado"
        )


def test_scheduling_agent_tools_are_registered():
    scheduling_agent = next(
        agent for agent in _specialist_agents() if agent.name == "SchedulingAgent"
    )
    tool_names = {tool.__name__ for tool in scheduling_agent.tools}
    assert tool_names == {"check_availability", "book_meeting"}


def test_qualification_agent_has_set_status_tool():
    qualification_agent = next(
        agent for agent in _specialist_agents() if agent.name == "QualificationAgent"
    )
    tool_names = {tool.__name__ for tool in qualification_agent.tools}
    assert tool_names == {"set_qualification_status"}


def _as_list(callback):
    """ADK aceita um único callback ou uma lista -- normaliza pra lista
    pra comparar de forma consistente nos testes."""
    if callback is None:
        return []
    return callback if isinstance(callback, list) else [callback]


def test_every_agent_masks_pii_before_calling_the_model():
    # Defesa em profundidade: TODO agente (Orchestrator + especialistas)
    # precisa mascarar PII antes de chamar seu próprio modelo — mesmo
    # que hoje só o Orchestrator receba texto cru do usuário, um futuro
    # tool de especialista (Parte 4+) poderia trazer PII de outro lugar.
    from app.agents.pii.masking import mask_pii

    all_agents = [root_agent, *_specialist_agents()]
    for agent in all_agents:
        assert mask_pii in _as_list(agent.before_model_callback), (
            f"{agent.name} não tem mask_pii registrado em before_model_callback"
        )


def test_every_agent_detects_prompt_injection():
    # Parte 3: mesma postura de defesa em profundidade do mask_pii.
    from app.agents.guardrails.prompt_injection import detect_prompt_injection

    all_agents = [root_agent, *_specialist_agents()]
    for agent in all_agents:
        assert detect_prompt_injection in _as_list(agent.before_model_callback), (
            f"{agent.name} não tem detect_prompt_injection registrado"
        )


def test_every_agent_validates_output_policy():
    # Parte 3: resolve o TODO de objection.py -- "nunca ofereça desconto"
    # precisa ser verificado na resposta de verdade, não só confiado ao
    # prompt. Defesa em profundidade em todo agente, não só onde o TODO
    # original estava (ver decisão de escopo da Parte 3).
    from app.agents.guardrails.output_policy import validate_output_policy

    all_agents = [root_agent, *_specialist_agents()]
    for agent in all_agents:
        assert validate_output_policy in _as_list(agent.after_model_callback), (
            f"{agent.name} não tem validate_output_policy registrado"
        )


def test_only_orchestrator_unmasks_pii():
    # unmask_pii só pode estar no Orchestrator -- ele é o único ponto de
    # saída pro usuário. Se algum especialista também desmascarasse, PII
    # voltaria pro contexto do Orchestrator antes da resposta final.
    from app.agents.pii.masking import unmask_pii

    assert unmask_pii in _as_list(root_agent.after_model_callback)

    for agent in _specialist_agents():
        assert unmask_pii not in _as_list(agent.after_model_callback), (
            f"{agent.name} não deveria desmascarar PII por conta própria"
        )


def test_only_scheduling_agent_has_action_allowlist():
    # enforce_action_allowlist só faz sentido em SchedulingAgent hoje --
    # é o único agente com uma ação (book_meeting) que precisa de
    # allowlist. Se aparecer em outro agente sem querer, ou sumir do
    # SchedulingAgent, isso pega a regressão.
    from app.agents.guardrails.action_allowlist import enforce_action_allowlist

    for agent in [root_agent, *_specialist_agents()]:
        callbacks = _as_list(agent.before_tool_callback)
        if agent.name == "SchedulingAgent":
            assert enforce_action_allowlist in callbacks, (
                "SchedulingAgent deveria ter enforce_action_allowlist em "
                "before_tool_callback"
            )
        else:
            assert enforce_action_allowlist not in callbacks, (
                f"{agent.name} não deveria ter enforce_action_allowlist"
            )
