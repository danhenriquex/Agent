"""
Unit tests for the interim guardrail that blocks unauthorized
transfer_to_agent attempts (see app/agents/_guardrails.py for context on
why this exists — adk-python#3850, still open upstream).

No LLM calls here: we construct fake LlmResponse objects directly to
test the detection/blocking logic in isolation.
"""

from unittest.mock import MagicMock

from google.adk.models import LlmResponse
from google.genai import types

from app.agents._guardrails import block_unauthorized_transfer
from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS


def _fake_context(agent_name: str = "KnowledgeAgent") -> MagicMock:
    ctx = MagicMock()
    ctx.agent_name = agent_name
    ctx.state = {}
    return ctx


def test_blocks_structured_transfer_call():
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="transfer_to_agent",
                        args={"agent_name": "QualificationAgent"},
                    )
                )
            ],
        )
    )
    ctx = _fake_context()

    result = block_unauthorized_transfer(ctx, response)

    assert result is not None
    assert "confirm" in result.content.parts[0].text.lower()
    assert len(ctx.state[STATE_GUARDRAIL_FLAGS]) == 1


def test_blocks_leaked_transfer_text():
    # This is the exact shape we saw in manual testing: the model
    # writing out the call syntax as plain text instead of a real
    # structured function call.
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text='transfer_to_agent {"agent_name": "QualificationAgent"}')],
        )
    )
    ctx = _fake_context()

    result = block_unauthorized_transfer(ctx, response)

    assert result is not None
    assert len(ctx.state[STATE_GUARDRAIL_FLAGS]) == 1


def test_allows_normal_response_through():
    response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(text="Our plans start at $49/month.")],
        )
    )
    ctx = _fake_context()

    result = block_unauthorized_transfer(ctx, response)

    assert result is None
    assert STATE_GUARDRAIL_FLAGS not in ctx.state
