"""
Unit tests for output policy validation. No LLM calls -- constructed
LlmResponse objects directly.
"""

from unittest.mock import MagicMock

from google.adk.models import LlmResponse
from google.genai import types

from app.agents.guardrails.output_policy import validate_output_policy
from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS


def _fake_context() -> MagicMock:
    ctx = MagicMock()
    ctx.agent_name = "ObjectionHandlingAgent"
    ctx.state = {}
    return ctx


def _response_with_text(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def test_blocks_percentage_discount():
    ctx = _fake_context()
    response = _response_with_text("Consigo te dar 15% de desconto se fechar hoje.")

    result = validate_output_policy(ctx, response)

    assert result is not None
    assert len(ctx.state[STATE_GUARDRAIL_FLAGS]) == 1


def test_blocks_special_price_offer():
    ctx = _fake_context()
    response = _response_with_text("Posso liberar um preço especial pra você.")

    result = validate_output_policy(ctx, response)

    assert result is not None


def test_blocks_discount_offer_phrasing():
    ctx = _fake_context()
    response = _response_with_text("Vou liberar um desconto especial pra fechar agora.")

    result = validate_output_policy(ctx, response)

    assert result is not None


def test_allows_normal_objection_handling_through():
    ctx = _fake_context()
    response = _response_with_text(
        "Entendo a preocupação com o orçamento. Posso te conectar com um "
        "account executive pra discutir as opções disponíveis."
    )

    result = validate_output_policy(ctx, response)

    assert result is None
    assert STATE_GUARDRAIL_FLAGS not in ctx.state
