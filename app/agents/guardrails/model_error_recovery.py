"""
Recuperação de um bug real, conhecido e ainda aberto a montante:
BerriAI/litellm#20543 -- modelos Claude ocasionalmente emitem os
argumentos de uma tool call como JSON duplicado e concatenado sem
separador (ex: '{"status": "x"}{"status": "x"}'), o que quebra o
parsing estrito de JSON.

ADK já tenta reparar formatos malformados em
lite_llm.py::_parse_tool_call_arguments (ast.literal_eval, chaves sem
aspas) antes de desistir -- mas nenhuma dessas estratégias cobre
"objeto JSON válido duplicado", então o erro original
(json.JSONDecodeError, msg="Extra data") sobe até quebrar a invocação
inteira do agente (DynamicNodeFailError).

Descoberto rodando test_golden_conversations.py::
test_qualification_flow_sets_status -- falhou duas vezes seguidas com
a MESMA assinatura de erro, o que indica que não é um acaso raro pra
esse par tool/conversa específico, e sim algo que vale ter uma
recuperação de verdade, não só documentação.

Este guardrail NÃO tenta reconstruir a tool call original (o nome da
função não está disponível de forma confiável no contexto de
on_model_error_callback, só os argumentos crus que falharam ao
parsear) -- em vez disso, faz uma degradação graciosa: evita o crash
duro e deixa a conversa continuar, em vez de travar a interação
inteira por causa de um bug de terceiros que nem o LiteLLM conseguiu
corrigir de forma definitiva ainda (a tentativa de correção deles,
#18667, foi revertida em #19243).
"""

import json

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

from ..session.state_schema import STATE_GUARDRAIL_FLAGS

_FALLBACK_TEXT = (
    "Desculpa, tive uma falha técnica processando isso agora. Pode "
    "repetir sua última mensagem?"
)


def recover_from_duplicated_tool_call_json(
    callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
) -> LlmResponse | None:
    """Detecta especificamente o padrão de erro do BerriAI/litellm#20543
    e degrada graciosamente em vez de deixar o agente inteiro travar.

    Retorna None (deixa o erro original propagar) pra qualquer outro
    tipo de erro -- este guardrail existe pra UM bug específico e
    conhecido, não é um catch-all genérico que esconderia problemas
    reais de verdade.
    """
    if not isinstance(error, json.JSONDecodeError):
        return None
    if "Extra data" not in str(error):
        return None

    flags = callback_context.state.get(STATE_GUARDRAIL_FLAGS, [])
    flags.append(
        f"{callback_context.agent_name}: recuperado de JSON duplicado em "
        "argumentos de tool call (bug a montante, ver "
        "github.com/BerriAI/litellm/issues/20543)"
    )
    callback_context.state[STATE_GUARDRAIL_FLAGS] = flags

    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=_FALLBACK_TEXT)])
    )
