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


def test_scheduling_agent_tools_are_registered():
    scheduling_agent = next(
        agent for agent in _specialist_agents() if agent.name == "SchedulingAgent"
    )
    tool_names = {tool.__name__ for tool in scheduling_agent.tools}
    assert tool_names == {"check_availability", "book_meeting"}
