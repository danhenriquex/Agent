"""
Unit tests for the action allowlist guardrail. No LLM calls -- uses the
real check_availability/book_meeting functions as the `tool` argument
(so tool.__name__ matches exactly what the real callback sees), with a
mocked ToolContext for state.
"""

from unittest.mock import MagicMock

from app.agents.guardrails.action_allowlist import enforce_action_allowlist
from app.agents.scheduling import book_meeting, check_availability
from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS, STATE_QUALIFICATION_STATUS


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

    result = enforce_action_allowlist(book_meeting, {"slot": "terça-feira às 10h"}, ctx)

    assert result is not None
    assert result["status"] == "blocked"
    assert "qualificar" in result["error_message"]
    assert len(ctx.state[STATE_GUARDRAIL_FLAGS]) == 1


def test_blocks_book_meeting_when_status_never_set():
    ctx = _fake_tool_context(qualification_status=None)

    result = enforce_action_allowlist(book_meeting, {"slot": "terça-feira às 10h"}, ctx)

    assert result is not None
    assert result["status"] == "blocked"


def test_allows_book_meeting_when_qualified():
    ctx = _fake_tool_context(qualification_status="qualified")

    result = enforce_action_allowlist(book_meeting, {"slot": "terça-feira às 10h"}, ctx)

    assert result is None  # None = deixa a tool real executar


def test_does_not_gate_check_availability():
    # check_availability não é uma ação sensível (só lê horários livres,
    # não confirma nada) -- não deveria ser bloqueada mesmo sem qualificação.
    ctx = _fake_tool_context(qualification_status="in_progress")

    result = enforce_action_allowlist(check_availability, {}, ctx)

    assert result is None


def test_blocked_message_includes_a_link():
    ctx = _fake_tool_context(qualification_status="disqualified")

    result = enforce_action_allowlist(book_meeting, {"slot": "terça-feira às 10h"}, ctx)

    assert "https://" in result["error_message"]
