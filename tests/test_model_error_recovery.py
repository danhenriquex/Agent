"""
Unit tests for the recovery guardrail that handles the known upstream
bug in BerriAI/litellm#20543 (duplicated JSON in tool call arguments).
No LLM calls -- constructs the exact real-world JSONDecodeError
signature directly.
"""

import json
from unittest.mock import MagicMock

from app.agents.guardrails.model_error_recovery import recover_from_duplicated_tool_call_json
from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS


def _fake_context() -> MagicMock:
    ctx = MagicMock()
    ctx.agent_name = "QualificationAgent"
    ctx.state = {}
    return ctx


def _real_duplicated_json_error() -> json.JSONDecodeError:
    """Reproduz a assinatura EXATA do erro real observado rodando
    test_golden_conversations.py -- os mesmos argumentos duplicados e
    concatenados sem separador."""
    malformed = (
        '{"status": "in_progress", "reasoning": "texto"}'
        '{"status": "in_progress", "reasoning": "texto"}'
    )
    try:
        json.loads(malformed)
    except json.JSONDecodeError as e:
        return e
    raise AssertionError("deveria ter levantado JSONDecodeError")


def test_recovers_from_real_duplicated_json_signature():
    ctx = _fake_context()
    error = _real_duplicated_json_error()

    result = recover_from_duplicated_tool_call_json(ctx, MagicMock(), error)

    assert result is not None
    assert result.content.parts[0].text
    assert len(ctx.state[STATE_GUARDRAIL_FLAGS]) == 1
    assert "litellm/issues/20543" in ctx.state[STATE_GUARDRAIL_FLAGS][0]


def test_does_not_swallow_unrelated_exceptions():
    ctx = _fake_context()

    result = recover_from_duplicated_tool_call_json(
        ctx, MagicMock(), ValueError("algo completamente diferente")
    )

    assert result is None
    assert STATE_GUARDRAIL_FLAGS not in ctx.state


def test_does_not_swallow_other_json_decode_errors():
    # Um JSONDecodeError de tipo diferente (ex: JSON genuinamente
    # malformado, não duplicado) não deveria ser mascarado -- esse
    # guardrail existe pra UM bug específico, não é um catch-all.
    ctx = _fake_context()
    other_error = json.JSONDecodeError("Expecting value", "", 0)

    result = recover_from_duplicated_tool_call_json(ctx, MagicMock(), other_error)

    assert result is None
    assert STATE_GUARDRAIL_FLAGS not in ctx.state
