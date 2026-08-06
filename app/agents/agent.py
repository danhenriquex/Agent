"""
Este arquivo existe só para satisfazer a convenção de descoberta do
`adk web`/`adk run`: o loader do ADK (is_single_agent_directory em
google/adk/cli/utils/agent_loader.py) procura especificamente por um
arquivo chamado `agent.py` (não `orchestrator.py`) diretamente nesta
pasta para tratá-la como "single agent mode" — sem ele, o ADK acha que
esta pasta é um DIRETÓRIO PAI contendo vários agentes, e escaneia suas
subpastas (`config/`, `session/`) como se cada uma fosse um agente
separado, encontra nenhuma com root_agent, e a UI mostra vazio.

O agente em si é definido em orchestrator.py — este arquivo só reexporta.
"""

from .orchestrator import root_agent  # noqa: F401
