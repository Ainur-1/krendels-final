"""
Демонстрационная база: проверочные ситуации и история линий за прошедшие смены.

История порождается эмулятором из зерна при первом запуске, а не лежит файлом в
репозитории: сотни изделий — это несколько мегабайт событий, которые воспроизводятся
за секунды из нескольких строк параметров.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zero_defect.config import DATA_DIR
from zero_defect.lines.emulator import history
from zero_defect.lines.model import LineConfig
from zero_defect.simulation.replay import load_steps

# Комплектов на линию в истории. Для L1 это около трёх смен при такте 12 минут: хватает,
# чтобы у каждого этапа была статистика, а у каждой смены — свои длительности.
HISTORY_SETS = 140


def _shift(value: object, delta: timedelta) -> object:
    if isinstance(value, dict):
        return {
            key: _shift(item, delta) if key not in _TIMES else _moved(item, delta)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_shift(item, delta) for item in value]
    return value


_TIMES = {"occurred_at", "received_at", "at", "captured_at"}


def _moved(value: object, delta: timedelta) -> object:
    if not isinstance(value, str):
        return value
    return (datetime.fromisoformat(value) + delta).isoformat()


def recent(steps: list[dict], end: datetime, gap: timedelta = timedelta(hours=2)) -> list[dict]:
    """Сдвигает проверочные ситуации целиком так, чтобы они закончились до end - gap.

    Ситуации записаны на даты хакатона и растянуты на полтора суток. В демонстрационной
    базе они должны лежать в прошлом, а не впереди текущего момента: иначе шкала
    времени уходит в будущее. Сдвиг одинаков для всех меток, поэтому порядок событий,
    опоздания и интервалы между ними не меняются.
    """

    latest = max(datetime.fromisoformat(step.get("received_at") or step["at"]) for step in steps)
    delta = (end - gap) - latest
    return [_shift(step, delta) for step in steps]


def demo_steps(configs: list[LineConfig], sets: int | None = None) -> list[dict]:
    end = datetime.now(UTC)
    steps = recent(load_steps(DATA_DIR / "demo" / "input.jsonl"), end)
    for seed, config in enumerate(configs, start=1):
        steps += history(config, sets if sets is not None else HISTORY_SETS, end, seed)
    return sorted(steps, key=lambda step: step.get("received_at") or step["at"])
