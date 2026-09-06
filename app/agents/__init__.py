from . import observability  # noqa: F401 -- instrumenta tracing antes de qualquer agente
from .orchestrator import root_agent

__all__ = ["root_agent"]
