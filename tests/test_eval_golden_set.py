"""
Layer 2 de testes pra Parte 6: o FORMATO do golden set é lógica pura
(pydantic + dados em memória), não precisa de LLM nem de rede pra
testar. Isso pega o tipo de regressão "alguém adicionou um item sem
critério estrutural" ou "um item aponta pra uma chave de state_schema
que não existe mais" antes de custar uma chamada de LLM pra descobrir.
"""

import pytest
from pydantic import ValidationError

from app.agents.session.state_schema import (
    STATE_ESCALATED,
    STATE_LAST_RETRIEVED_CONTEXT,
    STATE_MEETING_SLOT,
    STATE_OBJECTIONS_RAISED,
    STATE_PII_TOKEN_MAP,
    STATE_QUALIFICATION_NOTES,
    STATE_QUALIFICATION_STATUS,
)
from eval.golden_set import CATEGORIES, GOLDEN_SET, GoldenSetItem, get_items_by_category


def test_golden_set_has_at_least_fifteen_items():
    assert len(GOLDEN_SET) >= 15


def test_golden_set_ids_are_unique():
    ids = [item.id for item in GOLDEN_SET]
    assert len(ids) == len(set(ids)), "IDs duplicados no golden set"


def test_every_category_has_at_least_one_item():
    for category in CATEGORIES:
        items = get_items_by_category(category)
        assert items, f"categoria {category!r} não tem nenhum item no golden set"


def test_every_item_has_non_empty_conversation_and_criteria():
    for item in GOLDEN_SET:
        assert item.conversation, f"{item.id}: conversation vazia"
        assert all(
            msg.strip() for msg in item.conversation
        ), f"{item.id}: mensagem vazia na conversa"
        assert item.qualitative_criteria.strip(), f"{item.id}: qualitative_criteria vazio"


def test_every_item_declares_at_least_one_structural_signal():
    for item in GOLDEN_SET:
        has_signal = any(
            [
                item.expected_state_keys,
                item.expected_state_values,
                item.forbidden_state_keys,
                item.expected_guardrail_flag_substrings,
            ]
        )
        assert has_signal, f"{item.id}: nenhum critério estrutural declarado"


def test_item_without_any_structural_signal_is_rejected():
    # Confirma que o validador de GoldenSetItem (não só a convenção do
    # golden set atual) recusa um item "só vibe" -- sem isso, um item
    # novo poderia esquecer o critério estrutural sem nenhum teste pegar.
    with pytest.raises(ValidationError):
        GoldenSetItem(
            id="sem-criterio-estrutural",
            category="persona_adherence",
            target="root",
            conversation=["oi"],
            qualitative_criteria="qualquer coisa",
        )


def test_reference_context_only_present_for_faithfulness_relevant_categories():
    # rag_faithfulness é a categoria onde reference_context importa de
    # verdade (é o que o judge usa pra pontuar a dimensão faithfulness,
    # ver eval/judge.py) -- confirma que todo item dessa categoria
    # efetivamente carrega um.
    for item in get_items_by_category("rag_faithfulness"):
        assert (
            item.reference_context
        ), f"{item.id}: categoria rag_faithfulness sem reference_context"


def test_to_langfuse_metadata_round_trips_expected_fields():
    item = GOLDEN_SET[0]
    metadata = item.to_langfuse_metadata()

    assert metadata["golden_set_id"] == item.id
    assert metadata["category"] == item.category
    assert metadata["target"] == item.target
    assert metadata["qualitative_criteria"] == item.qualitative_criteria
    assert metadata["expected_state_keys"] == item.expected_state_keys


def test_state_key_literals_match_real_state_schema_constants():
    # golden_set.py repete as chaves de state_schema como string literal
    # (de propósito, pra continuar importável sem puxar app.agents --
    # ver comentário em eval/golden_set.py) -- este teste é o que
    # garante que essas strings não driftam da fonte real se
    # state_schema.py for editado.
    real_keys = {
        STATE_QUALIFICATION_STATUS,
        STATE_QUALIFICATION_NOTES,
        STATE_LAST_RETRIEVED_CONTEXT,
        STATE_OBJECTIONS_RAISED,
        STATE_MEETING_SLOT,
        STATE_ESCALATED,
        STATE_PII_TOKEN_MAP,
    }

    used_keys: set[str] = set()
    for item in GOLDEN_SET:
        used_keys.update(item.expected_state_keys)
        used_keys.update(item.expected_state_values.keys())
        used_keys.update(item.forbidden_state_keys)

    unknown = used_keys - real_keys
    assert not unknown, f"chaves usadas no golden set não batem com state_schema real: {unknown}"


def test_targets_are_valid_agent_identifiers():
    valid_targets = {"root", "qualification", "knowledge", "objection", "scheduling", "escalate"}
    for item in GOLDEN_SET:
        assert item.target in valid_targets, f"{item.id}: target inválido {item.target!r}"
