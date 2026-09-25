"""
Демонстрационная база: проверочные ситуации и история линий за прошедшие смены.

История порождается эмулятором из зерна при первом запуске, а не лежит файлом в
репозитории: сотни изделий — это несколько мегабайт событий, которые воспроизводятся
за секунды из нескольких строк параметров.
"""

from __future__ import annotations

from datetime import UTC, datetime

from zero_defect.config import DATA_DIR
from zero_defect.lines.emulator import history
from zero_defect.lines.model import LineConfig
from zero_defect.simulation.replay import load_steps

# Комплектов на линию в истории. Для L1 это около трёх смен при такте 12 минут: хватает,
# чтобы у каждого этапа была статистика, а у каждой смены — свои длительности.
HISTORY_SETS = 140


def demo_steps(configs: list[LineConfig], sets: int | None = None) -> list[dict]:
    steps = load_steps(DATA_DIR / "demo" / "input.jsonl")
    end = datetime.now(UTC)
    for seed, config in enumerate(configs, start=1):
        steps += history(config, sets if sets is not None else HISTORY_SETS, end, seed)
    return sorted(steps, key=lambda step: step.get("received_at") or step["at"])
