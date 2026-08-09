"""
Output policy validation (after_model_callback).

Resolves the TODO left in objection.py: "'nunca ofereça desconto'
precisa virar um guardrail verificável... instrução sozinha não é
garantia contra prompt injection." An instruction in the prompt is a
request, not an enforcement — a well-crafted injection can talk a
model out of following it. This checks the model's actual OUTPUT
against policy, regardless of what led to it.

Scope: every agent (defense in depth), not just ObjectionHandlingAgent
-- same posture as the transfer guardrail and mask_pii in Parts 1/2.
Today only ObjectionHandlingAgent's prompt invites discount talk, but
KnowledgeAgent will discuss real pricing once Part 4 adds RAG, and this
should already be in place when that happens rather than added later.
"""

import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.genai import types

from ..session.state_schema import STATE_GUARDRAIL_FLAGS

_FORBIDDEN_PATTERNS = [
    re.compile(r"\d{1,3}\s*%\s*(de\s+)?desconto", re.IGNORECASE),
    re.compile(r"desconto\s+de\s+\d{1,3}\s*%", re.IGNORECASE),
    re.compile(r"consigo\s+(fazer|dar|oferecer)\s+um\s+pre[çc]o", re.IGNORECASE),
    re.compile(r"pre[çc]o\s+especial\s+pra\s+voc[eê]", re.IGNORECASE),
    re.compile(r"vou\s+liberar\s+(um\s+)?desconto", re.IGNORECASE),
    re.compile(r"posso\s+(te\s+)?dar\s+(um\s+)?desconto", re.IGNORECASE),
]

_POLICY_VIOLATION_TEXT = (
    "Posso te conectar com um account executive pra discutir condições "
    "comerciais específicas. Enquanto isso, posso ajudar com outras dúvidas?"
)


def validate_output_policy(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    if not llm_response.content or not llm_response.content.parts:
        return None

    for part in llm_response.content.parts:
        if part.text is None:
            continue

        for pattern in _FORBIDDEN_PATTERNS:
            if pattern.search(part.text):
                flags = callback_context.state.get(STATE_GUARDRAIL_FLAGS, [])
                flags.append(
                    f"{callback_context.agent_name}: resposta bloqueada por "
                    f"política de saída (padrão: {pattern.pattern[:40]})"
                )
                callback_context.state[STATE_GUARDRAIL_FLAGS] = flags

                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=_POLICY_VIOLATION_TEXT)],
                    )
                )

    return None
