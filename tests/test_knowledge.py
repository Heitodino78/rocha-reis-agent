"""Carga local segura; dados oficiais continuam desconhecidos."""

import json
from pathlib import Path

import pytest

from rocha_reis_agent.domain.contracts import SimulationError
from rocha_reis_agent.knowledge import load_synthetic_knowledge


def test_fixture_loads_decimal_price_and_marks_synthetic(knowledge):
    assert knowledge.synthetic
    assert str(knowledge.services[0].price) == "45.00"
    assert knowledge.services[1].price is None


@pytest.mark.parametrize("content", [
    "{invalid json}",
    '{"synthetic": false, "name": "DO_NOT_ECHO_PRIVATE"}',
    '{"synthetic": true, "schema_version": 2}',
    '{"synthetic": "yes"}',
    '{"synthetic": true, "schema_version": true}',
    '{"synthetic": true, "name": "   "}',
    '{"synthetic": true, "name": "\\u001b[31mDO_NOT_ECHO_PRIVATE"}',
    '{"synthetic": true, "unexpected": "DO_NOT_ECHO_PRIVATE"}',
    '{"synthetic": true, "services": [{"id":"x", "name":"x", "price":"-1"}]}',
    '{"synthetic": true, "services": [{"id":"x", "name":"x"}, {"id":"x", "name":"y"}]}',
    "x" * 100001,
])
def test_invalid_or_non_synthetic_knowledge_is_rejected_without_content(tmp_path: Path, content):
    path = tmp_path / "knowledge.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SimulationError) as caught:
        load_synthetic_knowledge(path)
    assert "DO_NOT_ECHO_PRIVATE" not in str(caught.value)


def test_missing_knowledge_file_has_safe_error(tmp_path: Path):
    with pytest.raises(SimulationError, match="JSON sintético válido"):
        load_synthetic_knowledge(tmp_path / "missing.json")


def test_custom_synthetic_knowledge_can_leave_all_business_data_pending(tmp_path: Path):
    path = tmp_path / "knowledge.json"
    path.write_text(json.dumps({"synthetic": True}), encoding="utf-8")
    knowledge = load_synthetic_knowledge(path)
    assert knowledge.name is None
    assert not knowledge.services
    assert knowledge.booking_link() is None
