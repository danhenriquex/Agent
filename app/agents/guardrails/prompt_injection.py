"""
Heuristic prompt injection detection (before_model_callback).

Scope decision for this version: heuristic only, no LLM-judge second
layer. Catches common/obvious injection attempts via pattern matching;
does not catch subtler manipulation. That trade-off is deliberate — a
judge layer adds latency and cost to every single message, and the
project explicitly chose to defer that until it's shown to be needed
(e.g. once Part 6's eval set can measure the false-negative rate this
misses).

Runs after mask_pii in the before_model_callback chain (order:
[mask_pii, detect_prompt_injection]) — injection patterns are about
instructive phrasing, not PII-shaped content, so masking first doesn't
interfere with detection, and privacy-first ordering is kept
consistent with Part 2.
"""

import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

from ..session.state_schema import STATE_GUARDRAIL_FLAGS

_INJECTION_PATTERNS = [
    re.compile(r"ignor[ea]\s+(todas?\s+)?(as\s+)?instru[cç][oõ]es", re.IGNORECASE),
    re.compile(r"esque[çc]a\s+(que\s+)?(voc[eê]\s+)?[ée]\s+um", re.IGNORECASE),
    re.compile(r"voc[eê]\s+agora\s+[ée]\s+um", re.IGNORECASE),
    re.compile(r"revele?\s+(seu|o)\s+system\s*prompt", re.IGNORECASE),
    re.compile(r"quais?\s+s[ãa]o\s+suas?\s+instru[cç][oõ]es", re.IGNORECASE),
    re.compile(r"mostre?\s+(seu|o)\s+prompt", re.IGNORECASE),
    re.compile(r"modo\s+desenvolvedor", re.IGNORECASE),
    re.compile(r"\bDAN\b"),  # jailbreak persona conhecida ("Do Anything Now")
    re.compile(r"finja\s+que\s+voc[eê]", re.IGNORECASE),
    re.compile(r"a\s+partir\s+de\s+agora,?\s+voc[eê]\s+(é|vai|deve)", re.IGNORECASE),
    re.compile(r"desconsidere\s+(suas?\s+)?(regras?|instru[cç][oõ]es)", re.IGNORECASE),
]

_REFUSAL_TEXT = (
    "Não posso seguir instruções que tentam alterar meu comportamento. "
    "Posso ajudar com dúvidas sobre nosso produto, agendamento ou "
    "qualquer outra coisa relacionada — como posso ajudar?"
)


def _last_user_text(llm_request: LlmRequest) -> str | None:
    """Extrai o texto da última mensagem com role='user' na conversa.

    Só avaliamos o turno mais recente do usuário, não o histórico
    inteiro a cada chamada -- mensagens antigas já passaram pelo filtro
    quando chegaram, reavaliar todo turno de novo só re-marcaria a
    mesma coisa repetidamente sem necessidade.
    """
    for content in reversed(llm_request.contents or []):
        if content.role == "user" and content.parts:
            return "".join(p.text or "" for p in content.parts)
    return None


def detect_prompt_injection(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    text = _last_user_text(llm_request)
    if not text:
        return None

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            flags = callback_context.state.get(STATE_GUARDRAIL_FLAGS, [])
            flags.append(
                f"{callback_context.agent_name}: possível prompt injection "
                f"detectado (padrão: {pattern.pattern[:40]})"
            )
            callback_context.state[STATE_GUARDRAIL_FLAGS] = flags

            return LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=_REFUSAL_TEXT)])
            )

    return None
