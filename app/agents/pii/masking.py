"""
before_model_callback / after_model_callback que implementam o
mascaramento de PII em todo o sistema, seguindo o desenho de
"perímetro": mascarar uma vez na entrada do OrchestratorAgent (e, como
defesa em profundidade, em todo agente — idempotente, não-operação em
texto já mascarado), desmascarar só uma vez, na saída do
OrchestratorAgent.

Camadas (ver arquitetura da Parte 2 no README):
  1. Reversível (token)      -> PERSON, EMAIL_ADDRESS, TELEFONE_BR
  2. Hash com salt (correlação, nunca reversível) -> CPF_BR, CNPJ_BR
  3. Bloqueio total da mensagem -> CREDIT_CARD

O mapa de tokens vive só em session.state[STATE_PII_TOKEN_MAP] — nunca é
serializado em log, trace ou qualquer lugar fora da sessão em execução.
"""

import os

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types
from presidio_anonymizer.entities import OperatorConfig

from ..session.state_schema import STATE_GUARDRAIL_FLAGS, STATE_PII_TOKEN_MAP
from .engine import SUPPORTED_ENTITIES, get_analyzer_engine, get_anonymizer_engine

_TOKEN_TIER_PREFIXES = {
    "PERSON": "PERSON",
    "EMAIL_ADDRESS": "EMAIL",
    "TELEFONE_BR": "PHONE",
}
_HASH_TIER_ENTITIES = {"CPF_BR", "CNPJ_BR"}
_BLOCK_TIER_ENTITIES = {"CREDIT_CARD"}

_BLOCKED_MESSAGE_TEXT = (
    "Por segurança, não consigo processar mensagens com dados de cartão. "
    "Se precisar tratar de pagamento, um especialista humano vai te ajudar "
    "com isso separadamente."
)


def _get_pii_hash_salt() -> bytes:
    """Salt fixo usado no hash de CPF/CNPJ (tier 2).

    Precisa ser fixo (não aleatório por sessão) pra permitir correlação
    entre sessões ("é o mesmo lead de antes?"), e precisa ser secreto
    pra isso realmente proteger contra força bruta -- CPF tem um espaço
    de busca pequeno (11 dígitos), então um hash sem salt secreto é
    praticamente reversível.
    """
    salt = os.environ.get("PII_HASH_SALT")
    if not salt:
        raise RuntimeError(
            "PII_HASH_SALT não configurado. É obrigatório: sem um salt fixo "
            "e secreto, o hash de CPF/CNPJ ou usaria salt aleatório por "
            "execução (quebrando correlação entre sessões) ou um salt "
            "hardcoded (anulando a proteção contra força bruta). Gere um "
            'com: python -c "import secrets; print(secrets.token_hex(32))" '
            "e guarde em .env (local) / Secret Manager (produção)."
        )
    salt_bytes = salt.encode()
    if len(salt_bytes) < 16:
        raise RuntimeError("PII_HASH_SALT precisa ter pelo menos 16 bytes (32 caracteres hex).")
    return salt_bytes


def _get_or_create_token(token_map: dict, prefix: str, original_value: str) -> str:
    """Reusa o token existente se esse valor já apareceu nesta sessão;
    senão cria um novo, numerado sequencialmente por prefixo."""
    for token, value in token_map.items():
        if value == original_value and token.startswith(f"[{prefix}_"):
            return token

    existing = [t for t in token_map if t.startswith(f"[{prefix}_")]
    new_token = f"[{prefix}_{len(existing) + 1}]"
    token_map[new_token] = original_value
    return new_token


def _build_operators(token_map: dict, entity_types_present: set[str]) -> dict:
    """Constrói operadores SÓ para os tipos de entidade que apareceram de
    verdade no texto -- não para todos os tipos conhecidos.

    Isso importa porque o salt (PII_HASH_SALT) só é necessário pro tier
    de hash (CPF/CNPJ). Construir o operador de hash incondicionalmente
    pra todo texto (mesmo um que só tem um nome de pessoa, sem CPF nem
    CNPJ) exigiria o salt pra mascarar QUALQUER PII, não só CPF/CNPJ --
    era exatamente esse o bug: um texto com só um PERSON já disparava
    "PII_HASH_SALT não configurado", mesmo sem nenhum CPF envolvido.
    """
    operators = {}

    for entity_type, prefix in _TOKEN_TIER_PREFIXES.items():
        if entity_type not in entity_types_present:
            continue
        operators[entity_type] = OperatorConfig(
            "custom",
            {"lambda": lambda text, p=prefix: _get_or_create_token(token_map, p, text)},
        )

    for entity_type in _HASH_TIER_ENTITIES:
        if entity_type not in entity_types_present:
            continue
        operators[entity_type] = OperatorConfig(
            "hash",
            {"hash_type": "sha256", "salt": _get_pii_hash_salt()},
        )

    return operators


def _mask_text(text: str, token_map: dict) -> tuple[str, bool]:
    """Retorna (texto_processado, foi_bloqueado). Se foi_bloqueado for
    True, texto_processado é irrelevante -- a mensagem inteira deve ser
    substituída pela resposta de bloqueio."""
    analyzer = get_analyzer_engine()
    results = analyzer.analyze(text=text, language="pt", entities=SUPPORTED_ENTITIES)

    if any(r.entity_type in _BLOCK_TIER_ENTITIES for r in results):
        return "", True

    relevant = [r for r in results if r.entity_type not in _BLOCK_TIER_ENTITIES]
    if not relevant:
        return text, False

    anonymizer = get_anonymizer_engine()
    entity_types_present = {r.entity_type for r in relevant}
    operators = _build_operators(token_map, entity_types_present)
    anonymized = anonymizer.anonymize(text=text, analyzer_results=relevant, operators=operators)
    return anonymized.text, False


def mask_pii(callback_context: CallbackContext, llm_request: LlmRequest) -> LlmResponse | None:
    """before_model_callback: mascara PII em todas as partes de texto da
    requisição antes de ir para o LLM. Retorna uma LlmResponse (curto-
    circuitando a chamada real) se alguma parte continha dado bloqueado
    (tier 3); senão modifica llm_request in-place e retorna None."""
    if not llm_request.contents:
        return None

    token_map = callback_context.state.get(STATE_PII_TOKEN_MAP, {})

    for content in llm_request.contents:
        if not content.parts:
            continue

        new_parts = []
        for part in content.parts:
            if part.text is None:
                new_parts.append(part)
                continue

            masked_text, blocked = _mask_text(part.text, token_map)

            if blocked:
                flags = callback_context.state.get(STATE_GUARDRAIL_FLAGS, [])
                flags.append(
                    f"{callback_context.agent_name}: mensagem bloqueada "
                    "(continha dado de cartão)"
                )
                callback_context.state[STATE_GUARDRAIL_FLAGS] = flags
                callback_context.state[STATE_PII_TOKEN_MAP] = token_map

                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text=_BLOCKED_MESSAGE_TEXT)],
                    )
                )

            new_parts.append(types.Part(text=masked_text))

        content.parts = new_parts

    callback_context.state[STATE_PII_TOKEN_MAP] = token_map
    return None


def unmask_pii(callback_context: CallbackContext, llm_response: LlmResponse) -> LlmResponse | None:
    """after_model_callback: reverte os tokens reversíveis (tier 1) de
    volta aos valores originais. Só deve ser registrado no
    OrchestratorAgent -- ver docstring de orchestrator.py."""
    if not llm_response.content or not llm_response.content.parts:
        return None

    token_map = callback_context.state.get(STATE_PII_TOKEN_MAP, {})
    if not token_map:
        return None

    changed = False
    new_parts = []
    for part in llm_response.content.parts:
        if part.text is None:
            new_parts.append(part)
            continue

        text = part.text
        for token, original in token_map.items():
            if token in text:
                text = text.replace(token, original)
                changed = True

        new_parts.append(types.Part(text=text))

    if not changed:
        return None

    return LlmResponse(content=types.Content(role="model", parts=new_parts))
