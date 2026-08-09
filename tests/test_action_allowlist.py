"""
Unit tests for the action allowlist guardrail. No LLM calls -- but
CRITICALLY, wraps check_availability/book_meeting in the real
FunctionTool class (same as ADK does internally) instead of passing the
raw functions directly.

This distinction matters: an earlier version of this test passed the
raw functions as `tool`, which have `__name__` -- masking a real bug
where the guardrail checked `tool.__name__` instead of `tool.name`
(the actual attribute FunctionTool exposes). That bug only surfaced in
real execution (test_golden_conversations.py), not here, because the
raw-function stand-in didn't match the real integration boundary's
shape. Wrapping in FunctionTool here closes that gap.
"""

from unittest.mock import MagicMock

from google.adk.tools import FunctionTool

from app.agents.guardrails.action_allowlist import enforce_action_allowlist
from app.agents.scheduling import book_meeting, check_availability
from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS, STATE_QUALIFICATION_STATUS

_book_meeting_tool = FunctionTool(book_meeting)
_check_availability_tool = FunctionTool(check_availability)


def _fake_tool_context(qualification_status: str | None) -> MagicMock:
    ctx = MagicMock()
    ctx.agent_name = "SchedulingAgent"
    ctx.state = {}
    if qualification_status is not None:
        ctx.state[STATE_QUALIFICATION_STATUS] = qualification_status
    ctx.session = MagicMock()
    ctx.session.id = "test-session-id-12345"
    return ctx


def test_blocks_book_meeting_when_not_qualified():
    ctx = _fake_tool_context(qualification_status="in_progress")

    result = enforce_action_allowlist(
        _book_meeting_tool, {"slot": "terça-feira às 10h"}, ctx
    )

    assert result is not None
    assert result["status"] == "blocked"
    assert "qualificar" in result["error_message"]
    assert len(ctx.state[STATE_GUARDRAIL_FLAGS]) == 1


def test_blocks_book_meeting_when_status_never_set():
    ctx = _fake_tool_context(qualification_status=None)

    result = enforce_action_allowlist(
        _book_meeting_tool, {"slot": "terça-feira às 10h"}, ctx
    )

    assert result is not None
    assert result["status"] == "blocked"


def test_allows_book_meeting_when_qualified():
    ctx = _fake_tool_context(qualification_status="qualified")

    result = enforce_action_allowlist(
        _book_meeting_tool, {"slot": "terça-feira às 10h"}, ctx
    )

    assert result is None  # None = deixa a tool real executar


def test_does_not_gate_check_availability():
    # check_availability não é uma ação sensível (só lê horários livres,
    # não confirma nada) -- não deveria ser bloqueada mesmo sem qualificação.
    ctx = _fake_tool_context(qualification_status="in_progress")

    result = enforce_action_allowlist(_check_availability_tool, {}, ctx)

    assert result is None


def test_blocked_message_includes_a_link():
    ctx = _fake_tool_context(qualification_status="disqualified")

    result = enforce_action_allowlist(
        _book_meeting_tool, {"slot": "terça-feira às 10h"}, ctx
    )

    assert "https://" in result["error_message"]


def test_would_have_caught_the_dunder_name_bug():
    # Regressão direta do bug real: tool.__name__ não existe em
    # FunctionTool (só em funções cruas). Se alguém reintroduzir
    # `tool.__name__` no guardrail, este teste falha com AttributeError
    # antes de qualquer chamada real de LLM revelar o problema.
    assert not hasattr(_book_meeting_tool, "__name__")
    assert _book_meeting_tool.name == "book_meeting"
