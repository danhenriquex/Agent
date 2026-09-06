"""
Layer 2 de testes pra Parte 6: check_structural() é lógica pura sobre
dicts (session.state sintético + metadata de um GoldenSetItem), não
precisa de agente real nem de LLM pra testar. `run_conversation` (a
única função deste módulo que roda um agente de verdade) não é testada
aqui -- ela só roda no pipeline real (`make eval-run` / `test-live`).
"""

from app.agents.session.state_schema import STATE_GUARDRAIL_FLAGS
from eval.runner import check_structural


def test_passes_when_expected_state_key_present():
    passed, reasons = check_structural(
        state={"qualification_status": "qualified"},
        metadata={"expected_state_keys": ["qualification_status"]},
    )
    assert passed
    assert reasons == []


def test_fails_when_expected_state_key_missing():
    passed, reasons = check_structural(
        state={},
        metadata={"expected_state_keys": ["qualification_status"]},
    )
    assert not passed
    assert any("qualification_status" in r for r in reasons)


def test_passes_when_expected_state_value_matches():
    passed, _reasons = check_structural(
        state={"qualification_status": "qualified"},
        metadata={"expected_state_values": {"qualification_status": "qualified"}},
    )
    assert passed


def test_fails_when_expected_state_value_mismatches():
    passed, reasons = check_structural(
        state={"qualification_status": "in_progress"},
        metadata={"expected_state_values": {"qualification_status": "qualified"}},
    )
    assert not passed
    assert any("in_progress" in r for r in reasons)


def test_passes_when_forbidden_state_key_absent():
    passed, _reasons = check_structural(
        state={},
        metadata={"forbidden_state_keys": ["escalated"]},
    )
    assert passed


def test_fails_when_forbidden_state_key_present():
    passed, reasons = check_structural(
        state={"escalated": True},
        metadata={"forbidden_state_keys": ["escalated"]},
    )
    assert not passed
    assert any("escalated" in r for r in reasons)


def test_passes_when_guardrail_flag_substring_found():
    passed, _reasons = check_structural(
        state={STATE_GUARDRAIL_FLAGS: ["OrchestratorAgent: possível prompt injection detectado"]},
        metadata={"expected_guardrail_flag_substrings": ["prompt injection"]},
    )
    assert passed


def test_fails_when_guardrail_flag_substring_missing():
    passed, reasons = check_structural(
        state={STATE_GUARDRAIL_FLAGS: ["algo completamente não relacionado"]},
        metadata={"expected_guardrail_flag_substrings": ["prompt injection"]},
    )
    assert not passed
    assert any("prompt injection" in r for r in reasons)


def test_fails_when_guardrail_flags_key_absent_entirely():
    passed, reasons = check_structural(
        state={},
        metadata={"expected_guardrail_flag_substrings": ["book_meeting"]},
    )
    assert not passed
    assert reasons


def test_passes_with_no_metadata_criteria_at_all():
    # metadata sem nenhuma das quatro chaves -- não deveria acontecer no
    # golden set real (GoldenSetItem exige pelo menos uma, ver
    # tests/test_eval_golden_set.py), mas check_structural em si deve
    # se comportar como "nada a checar, passou" nesse caso, não explodir.
    passed, reasons = check_structural(state={"qualquer": "coisa"}, metadata={})
    assert passed
    assert reasons == []


def test_multiple_failures_are_all_reported_not_just_the_first():
    passed, reasons = check_structural(
        state={},
        metadata={
            "expected_state_keys": ["a", "b"],
            "forbidden_state_keys": ["c"],
        },
    )
    assert not passed
    assert len(reasons) == 2
