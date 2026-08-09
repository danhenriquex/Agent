"""
Layer 2 de testes: as funções de tool que são Python puro (sem chamada
de LLM) merecem teste direto, não só indireto via guardrail. Rápido,
grátis, e pega regressão na lógica determinística antes de qualquer
chamada de API real.

Ver README > "Estratégia de testes" para onde isso se encaixa na
pirâmide de 4 camadas (estrutural → lógica de tool → conversa dourada →
eval set com LLM-judge).
"""

from unittest.mock import MagicMock

from app.agents.qualification import set_qualification_status
from app.agents.scheduling import book_meeting, check_availability
from app.agents.session.state_schema import STATE_QUALIFICATION_STATUS


def _fake_tool_context() -> MagicMock:
    ctx = MagicMock()
    ctx.state = {}
    return ctx


# --- check_availability ---


def test_check_availability_returns_success_with_slots():
    result = check_availability()

    assert result["status"] == "success"
    assert len(result["available_slots"]) == 3


# --- book_meeting ---


def test_book_meeting_confirms_valid_slot():
    result = book_meeting("terça-feira às 10h")

    assert result["status"] == "success"
    assert result["confirmed_slot"] == "terça-feira às 10h"


def test_book_meeting_rejects_invalid_slot():
    result = book_meeting("sexta-feira às 20h")

    assert result["status"] == "error"
    assert "não está disponível" in result["error_message"]


def test_book_meeting_slot_list_matches_check_availability():
    # Trava a consistência entre as duas tools -- se alguém mudar
    # _MOCK_SLOTS num lugar só, isso pega a divergência.
    available = check_availability()["available_slots"]
    for slot in available:
        assert book_meeting(slot)["status"] == "success"


# --- set_qualification_status ---


def test_set_qualification_status_accepts_valid_status():
    ctx = _fake_tool_context()

    result = set_qualification_status("qualified", "orçamento e prazo confirmados", ctx)

    assert result["status"] == "success"
    assert result["recorded_status"] == "qualified"
    assert ctx.state[STATE_QUALIFICATION_STATUS] == "qualified"


def test_set_qualification_status_rejects_invalid_status():
    ctx = _fake_tool_context()

    result = set_qualification_status("super_qualified", "não é um status real", ctx)

    assert result["status"] == "error"
    assert STATE_QUALIFICATION_STATUS not in ctx.state


def test_set_qualification_status_all_valid_values_accepted():
    for status in ("qualified", "disqualified", "in_progress"):
        ctx = _fake_tool_context()
        result = set_qualification_status(status, "teste", ctx)
        assert result["status"] == "success"
        assert ctx.state[STATE_QUALIFICATION_STATUS] == status
