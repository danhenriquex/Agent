"""
Action allowlist (before_tool_callback).

Resolves the TODO left in scheduling.py: "antes de confirmar de
verdade, isso deveria passar por um before_tool_callback validando que
o lead está QUALIFICADO — allowlist de AÇÃO, não só allowlist de tool
disponível." This is a different concern than a content guardrail: it's
about what the system is ALLOWED TO EXECUTE, not what it says.

When blocked, we simulate a redirect to a qualification flow (a fake
link — there's no real qualification web form in this project) rather
than just refusing. The tool's own error-result contract (matching the
shape book_meeting already uses for its own "slot not available" case)
lets the calling agent's model turn this into natural language, instead
of us hardcoding the exact user-facing sentence here.
"""

from google.adk.tools import ToolContext

from ..session.state_schema import STATE_GUARDRAIL_FLAGS, STATE_QUALIFICATION_STATUS

_GATED_TOOLS = {"book_meeting"}

_QUALIFICATION_LINK_BASE = "https://sdr-bot.exemplo.com/qualificar"


def enforce_action_allowlist(tool, args: dict, tool_context: ToolContext) -> dict | None:
    """Retorna um dict (substituindo o resultado real da tool) se a ação
    for bloqueada; retorna None para deixar a tool executar normalmente.
    """
    if tool.__name__ not in _GATED_TOOLS:
        return None

    status = tool_context.state.get(STATE_QUALIFICATION_STATUS)
    if status == "qualified":
        return None

    flags = tool_context.state.get(STATE_GUARDRAIL_FLAGS, [])
    flags.append(
        f"{tool_context.agent_name}: {tool.__name__} bloqueado — lead "
        f"ainda não qualificado (status atual: {status!r})"
    )
    tool_context.state[STATE_GUARDRAIL_FLAGS] = flags

    lead_token = tool_context.session.id[:8] if tool_context.session else "lead"

    return {
        "status": "blocked",
        "error_message": (
            "Este lead ainda não completou a qualificação. Antes de "
            "agendar, envie o link para completar o perfil: "
            f"{_QUALIFICATION_LINK_BASE}?lead={lead_token}"
        ),
    }
