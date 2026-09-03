"""
Layer 2 de testes pra Parte 6: parsing da resposta do juiz e montagem
do prompt são lógica pura (JSON + string), não precisam de chamada de
LLM pra testar. `judge_response` (a única função que faz I/O de rede)
deliberadamente NÃO é testada aqui -- ela só roda no pipeline real
(`make eval-run`), nunca em `make test`.
"""

import pytest

from eval.judge import JudgeScore, build_judge_prompt, parse_judge_response


def test_build_judge_prompt_includes_conversation_response_and_criteria():
    prompt = build_judge_prompt(
        conversation=["oi", "quanto custa?"],
        response_text="O plano Growth custa R$19/funcionário/mês.",
        qualitative_criteria="responde o preço corretamente",
    )

    assert "oi" in prompt
    assert "quanto custa?" in prompt
    assert "O plano Growth custa R$19/funcionário/mês." in prompt
    assert "responde o preço corretamente" in prompt


def test_build_judge_prompt_omits_reference_context_section_when_absent():
    prompt = build_judge_prompt(
        conversation=["oi"], response_text="Oi!", qualitative_criteria="crit"
    )
    assert "CONTEXTO DE REFERÊNCIA" not in prompt


def test_build_judge_prompt_includes_reference_context_when_present():
    prompt = build_judge_prompt(
        conversation=["oi"],
        response_text="Oi!",
        qualitative_criteria="crit",
        reference_context="Starter custa R$12/funcionário/mês.",
    )
    assert "CONTEXTO DE REFERÊNCIA" in prompt
    assert "Starter custa R$12/funcionário/mês." in prompt


def test_parse_judge_response_accepts_clean_json():
    raw = (
        '{"criteria_adherence": 4, "tone_and_persona": 5, "faithfulness": null, '
        '"justification": "Boa resposta, tom adequado."}'
    )
    score = parse_judge_response(raw)

    assert score.criteria_adherence == 4
    assert score.tone_and_persona == 5
    assert score.faithfulness is None
    assert "Boa resposta" in score.justification


def test_parse_judge_response_strips_markdown_code_fence():
    raw = (
        "```json\n"
        '{"criteria_adherence": 3, "tone_and_persona": 3, "faithfulness": 2, '
        '"justification": "Alegação sem suporte no contexto."}\n'
        "```"
    )
    score = parse_judge_response(raw)

    assert score.criteria_adherence == 3
    assert score.faithfulness == 2


def test_parse_judge_response_rejects_malformed_json():
    with pytest.raises(ValueError):
        parse_judge_response("isso não é JSON de jeito nenhum")


def test_parse_judge_response_rejects_json_missing_required_fields():
    with pytest.raises(ValueError):
        parse_judge_response('{"criteria_adherence": 4}')


def test_parse_judge_response_rejects_score_out_of_range():
    raw = (
        '{"criteria_adherence": 9, "tone_and_persona": 5, "faithfulness": null, '
        '"justification": "nota fora da escala"}'
    )
    with pytest.raises(ValueError):
        parse_judge_response(raw)


def test_judge_score_overall_excludes_faithfulness_when_none():
    score = JudgeScore(
        criteria_adherence=4, tone_and_persona=2, faithfulness=None, justification="x"
    )
    assert score.overall == pytest.approx(3.0)


def test_judge_score_overall_includes_faithfulness_when_present():
    score = JudgeScore(criteria_adherence=4, tone_and_persona=2, faithfulness=3, justification="x")
    assert score.overall == pytest.approx(3.0)
