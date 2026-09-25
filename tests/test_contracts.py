"""
Контракт событий: версии, коды ошибок и проверка совместимости.

Постановка перечисляет пять отклонений от контракта, о которых надо сказать, как
система себя ведёт. Здесь по тесту на каждое — и на то, что поведение одинаково
для обеих принимаемых версий.
"""

from __future__ import annotations

import copy
import json

from tests.helpers import NOW, message
from zero_defect.config import CONTRACTS_DIR, SCHEMAS_DIR
from zero_defect.contracts.compat import BREAKING, CONSUMER_UPDATE, compare, is_breaking
from zero_defect.ingest.validation import parse_event


def codes(result) -> list[str]:
    return sorted(error.code for error in result.errors)


def test_examples_of_both_versions_are_accepted():
    for name in ("event_inspection_v1.0.json", "event_operator_action_v1.1.json"):
        raw = json.loads((CONTRACTS_DIR / "examples" / name).read_text(encoding="utf-8"))
        result = parse_event(raw, NOW)
        assert result.ok, result.errors


def test_unknown_version_is_rejected_whole():
    result = parse_event(message(schema_version="2.0"), NOW)
    assert codes(result) == ["unknown_schema_version"]


def test_missing_required_field_names_the_field():
    raw = message()
    del raw["item_type_id"]
    result = parse_event(raw, NOW)
    assert [(error.field, error.code) for error in result.errors] == [
        ("item_type_id", "missing_field")
    ]


def test_all_problems_are_reported_at_once():
    raw = message(event_type="inspection_reported", inspection_result="maybe")
    result = parse_event(raw, NOW)
    assert {"missing_field", "unknown_enum_value"} <= set(codes(result))
    assert len(result.errors) >= 3


def test_unknown_enum_value_has_its_own_code():
    raw = message(event_type="machine_state", equipment_id="CNC-01", machine_state="overheated")
    assert codes(parse_event(raw, NOW)) == ["unknown_enum_value"]


def test_value_added_in_1_1_is_unknown_in_1_0():
    action = dict(event_type="operator_action", operator_id="OP-1", action_type="tool_change")
    assert parse_event(message(schema_version="1.1", **action), NOW).ok
    assert codes(parse_event(message(schema_version="1.0", **action), NOW)) == [
        "unknown_enum_value"
    ]


def test_unknown_optional_field_is_kept_as_warning():
    result = parse_event(message(humidity_pct=41), NOW)
    assert result.ok
    assert [warning.code for warning in result.warnings] == ["unknown_optional_field"]


def test_compat_classifies_the_real_version_step():
    old = json.loads((SCHEMAS_DIR / "v1.0.schema.json").read_text(encoding="utf-8"))
    new = json.loads((SCHEMAS_DIR / "v1.1.schema.json").read_text(encoding="utf-8"))
    changes = compare(old, new)
    assert not is_breaking(changes)
    assert any(change.kind == CONSUMER_UPDATE for change in changes)


def test_compat_catches_breaking_changes():
    old = json.loads((SCHEMAS_DIR / "v1.0.schema.json").read_text(encoding="utf-8"))
    new = copy.deepcopy(old)
    del new["$defs"]["InspectionReported"]["properties"]["confidence"]
    new["$defs"]["OperationStarted"]["required"].append("operator_id")
    new["$defs"]["InspectionResult"]["enum"].remove("not_assessable")
    kinds = {(change.where, change.kind) for change in compare(old, new)}
    assert ("InspectionReported.confidence", BREAKING) in kinds
    assert ("OperationStarted.operator_id", BREAKING) in kinds
    assert ("InspectionResult", BREAKING) in kinds
