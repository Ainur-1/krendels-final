"""
Восемь проверочных сценариев постановки сходятся с ожиданиями, записанными отдельно.

Тот же прогон делает scripts/check_scenarios.py; здесь он встроен в набор тестов, чтобы
красный сценарий останавливал CI вместе с остальными проверками.
"""

from __future__ import annotations

import pytest

from zero_defect.simulation.checker import check
from zero_defect.simulation.replay import load_steps
from zero_defect.simulation.scenarios import SCENARIOS, build


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_matches_expectation(name):
    mismatches, _ = check(name)
    assert mismatches == []


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_committed_input_matches_builder(name):
    """Файлы сценариев в data/ не разошлись с построителем, из которого порождены."""

    from zero_defect.config import SCENARIOS_DIR

    assert load_steps(SCENARIOS_DIR / name / "input.jsonl") == build(name)
