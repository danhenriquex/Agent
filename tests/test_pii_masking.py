"""
Testes da camada de mascaramento de PII (Parte 2). Como em
test_guardrails.py: construímos LlmRequest/LlmResponse reais direto,
sem nenhuma chamada de LLM -- a lógica de detecção/mascaramento roda
localmente via Presidio + spaCy.

Nota: estes testes carregam o modelo spaCy pt_core_news_lg na primeira
vez que tocam PERSON (~15s, uma vez por sessão de teste, cacheado depois
disso pelo singleton em engine.py). CPF/CNPJ/telefone não precisam do
modelo carregado (são recognizers baseados em regex/phonenumbers).
"""

import os
from unittest.mock import MagicMock

import pytest
from google.adk.models import LlmRequest
from google.genai import types

from app.agents.pii.masking import mask_pii, unmask_pii
from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS, STATE_PII_TOKEN_MAP

# Salt de teste -- nunca usar isso fora de testes. Setado antes de
# qualquer teste que dependa de hashing (CPF/CNPJ).
os.environ.setdefault("PII_HASH_SALT", "test-salt-not-for-production-use-32b")


def _fake_context(agent_name: str = "OrchestratorAgent") -> MagicMock:
    ctx = MagicMock()
    ctx.agent_name = agent_name
    ctx.state = {}
    return ctx


def _request_with_text(text: str) -> LlmRequest:
    return LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text=text)])]
    )


def test_masks_person_name():
    ctx = _fake_context()
    request = _request_with_text("Oi, meu nome é Danilo Henrique, quero saber mais.")

    result = mask_pii(ctx, request)

    assert result is None  # não bloqueou, deixa a request (agora mascarada) seguir
    masked_text = request.contents[0].parts[0].text
    assert "Danilo Henrique" not in masked_text
    assert "[PERSON_1]" in masked_text
    assert ctx.state[STATE_PII_TOKEN_MAP]["[PERSON_1]"] == "Danilo Henrique"


def test_masks_email():
    ctx = _fake_context()
    request = _request_with_text("meu email é danilo@empresa.com.br")

    mask_pii(ctx, request)

    masked_text = request.contents[0].parts[0].text
    assert "danilo@empresa.com.br" not in masked_text
    assert "[EMAIL_1]" in masked_text


def test_masks_phone():
    ctx = _fake_context()
    request = _request_with_text("pode ligar no (83) 99419-8558")

    mask_pii(ctx, request)

    masked_text = request.contents[0].parts[0].text
    assert "99419-8558" not in masked_text
    assert "[PHONE_1]" in masked_text


def test_reuses_same_token_for_repeated_value():
    ctx = _fake_context()
    request = _request_with_text("Danilo aqui. É, o Danilo mesmo, de novo.")

    mask_pii(ctx, request)

    masked_text = request.contents[0].parts[0].text
    # Duas menções ao mesmo nome devem virar o MESMO token, não
    # [PERSON_1] e [PERSON_2] -- senão a conversa fica incoerente.
    assert masked_text.count("[PERSON_1]") == 2
    assert "[PERSON_2]" not in masked_text


def test_cpf_is_hashed_not_tokenized():
    ctx = _fake_context()
    request = _request_with_text("meu cpf é 111.444.777-35")

    mask_pii(ctx, request)

    masked_text = request.contents[0].parts[0].text
    assert "111.444.777-35" not in masked_text
    # CPF é tier 2 (hash), não deveria aparecer como token reversível
    assert "[CPF" not in masked_text
    # Não deveria ter sido adicionado ao mapa de tokens reversíveis
    assert not any("111.444.777-35" == v for v in ctx.state.get(STATE_PII_TOKEN_MAP, {}).values())


def test_cpf_hash_is_deterministic_with_same_salt():
    ctx1 = _fake_context()
    ctx2 = _fake_context()
    request1 = _request_with_text("cpf 111.444.777-35")
    request2 = _request_with_text("cpf 111.444.777-35")

    mask_pii(ctx1, request1)
    mask_pii(ctx2, request2)

    text1 = request1.contents[0].parts[0].text
    text2 = request2.contents[0].parts[0].text
    # Mesmo CPF, mesmo salt (fixo via env var) -> mesmo hash em sessões
    # diferentes -- é isso que permite correlação sem guardar o CPF cru.
    assert text1 == text2


def test_credit_card_blocks_entire_message():
    ctx = _fake_context()
    request = _request_with_text("meu cartão é 4532 0151 1283 0366, pode cobrar?")

    result = mask_pii(ctx, request)

    assert result is not None  # curto-circuitou a chamada ao LLM
    assert "cancel" not in result.content.parts[0].text.lower()
    assert len(ctx.state[STATE_GUARDRAIL_FLAGS]) == 1
    assert "cartão" in ctx.state[STATE_GUARDRAIL_FLAGS][0]


def test_ordinary_text_passes_through_unchanged():
    ctx = _fake_context()
    request = _request_with_text("gostaria de saber sobre os planos de vocês")
    original_text = request.contents[0].parts[0].text

    result = mask_pii(ctx, request)

    assert result is None
    assert request.contents[0].parts[0].text == original_text


def test_unmask_reverses_tokens():
    ctx = _fake_context()
    # Simula um token map já populado por uma mensagem anterior
    ctx.state[STATE_PII_TOKEN_MAP] = {"[PERSON_1]": "Danilo Henrique"}

    response = types.Content(
        role="model",
        parts=[types.Part(text="Olá [PERSON_1], tudo bem?")],
    )
    from google.adk.models import LlmResponse

    llm_response = LlmResponse(content=response)

    result = unmask_pii(ctx, llm_response)

    assert result is not None
    assert result.content.parts[0].text == "Olá Danilo Henrique, tudo bem?"


def test_unmask_is_noop_without_token_map():
    ctx = _fake_context()
    from google.adk.models import LlmResponse

    llm_response = LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text="Olá!")])
    )

    result = unmask_pii(ctx, llm_response)

    assert result is None


def test_missing_salt_raises_clear_error(monkeypatch):
    monkeypatch.delenv("PII_HASH_SALT", raising=False)
    ctx = _fake_context()
    request = _request_with_text("cpf 111.444.777-35")

    with pytest.raises(RuntimeError, match="PII_HASH_SALT"):
        mask_pii(ctx, request)
