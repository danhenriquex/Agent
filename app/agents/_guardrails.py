"""
Interim guardrail for a known, currently OPEN bug in ADK:
disallow_transfer_to_peers / disallow_transfer_to_parent only edit the
agent's prompt text — they do not remove the transfer_to_agent tool from
what the model is actually allowed to call. A sub-agent can still attempt
a transfer to a sibling it knows about from conversation history.
Reference: https://github.com/google/adk-python/issues/3850 (open)

We observed two variants of this in manual testing:
1. A real structured function call with name="transfer_to_agent".
2. The model "leaking" the call syntax as plain text instead of a proper
   structured call (what actually happened with KnowledgeAgent).

This is intentionally minimal and lives outside the real guardrail
system that Part 3 will build (prompt injection detection, action
allowlists, etc.) — treat this as a stopgap, not the final design. When
Part 3 lands, this will likely be folded into the general mechanism.
"""

import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.genai import types

from app.session.state_schema import STATE_GUARDRAIL_FLAGS

_LEAKED_TRANSFER_PATTERN = re.compile(r"transfer_to_agent\s*\{")

_FALLBACK_TEXT = "Let me confirm that with the team and get back to you shortly."


def block_unauthorized_transfer(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    """Blocks transfer_to_agent attempts from a sub-agent that shouldn't
    be routing on its own. Returning a non-None LlmResponse replaces the
    model's original response; returning None lets it through unchanged.
    """
    if not llm_response.content or not llm_response.content.parts:
        return None

    for part in llm_response.content.parts:
        is_structured_transfer_call = (
            part.function_call is not None
            and part.function_call.name == "transfer_to_agent"
        )
        is_leaked_transfer_text = part.text is not None and _LEAKED_TRANSFER_PATTERN.search(
            part.text
        )

        if is_structured_transfer_call or is_leaked_transfer_text:
            flags = callback_context.state.get(STATE_GUARDRAIL_FLAGS, [])
            flags.append(
                f"{callback_context.agent_name} attempted an unauthorized "
                "transfer_to_agent (blocked by interim guardrail)"
            )
            callback_context.state[STATE_GUARDRAIL_FLAGS] = flags

            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=_FALLBACK_TEXT)],
                )
            )

    return None
