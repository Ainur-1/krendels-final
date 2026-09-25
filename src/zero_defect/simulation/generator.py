"""
Воспроизводимый генератор потока событий для проверки масштабирования.

Параметры — интенсивность (изделий в минуту), число источников (линий), доля дефектов,
повторов и опозданий, зерно случайности. Одно и то же зерно всегда даёт тот же поток,
поэтому прогоны с одним и несколькими обработчиками сравниваются на одинаковых данных.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from zero_defect.ingest.pipeline import Delivery
from zero_defect.simulation.line import MSK

DEFECTS = ("POROSITY", "LACK_OF_FUSION", "CRACK", "BURR")


@dataclass(frozen=True)
class GeneratorConfig:
    items: int = 200
    sources: int = 4
    items_per_minute: float = 6.0
    defect_rate: float = 0.08
    duplicate_rate: float = 0.05
    late_rate: float = 0.03
    seed: int = 7
    start: datetime = datetime(2026, 9, 26, 8, 0, tzinfo=MSK)


def generate(config: GeneratorConfig) -> tuple[list[Delivery], int]:
    """Поток доставок по времени поступления и число уникальных событий в нём."""

    rng = random.Random(config.seed)
    deliveries: list[Delivery] = []
    unique = 0
    sequences: dict[str, int] = {}

    def emit(message: dict, occurred: datetime) -> None:
        nonlocal unique
        message = {key: value for key, value in message.items() if value is not None}
        source = message["source_id"]
        sequences[source] = sequences.get(source, 0) + 1
        message.update(
            {
                "schema_version": "1.0",
                "occurred_at": occurred.isoformat(),
                "sequence_no": sequences[source],
            }
        )
        unique += 1
        delay = rng.uniform(0.5, 5.0)
        if rng.random() < config.late_rate:
            delay += rng.uniform(600, 3600)
        deliveries.append(Delivery(message, occurred + timedelta(seconds=delay)))
        if rng.random() < config.duplicate_rate:
            deliveries.append(Delivery(dict(message), occurred + timedelta(seconds=delay + 30)))

    for index in range(config.items):
        line = f"L{index % config.sources + 1}"
        item = f"G-{index:05d}"
        moment = config.start + timedelta(minutes=index / config.items_per_minute)
        run_mill, run_weld = f"GR-{index:05d}-M", f"GR-{index:05d}-W"
        base = {"item_id": item, "line_id": line}

        def event(
            kind: str,
            source: str,
            occurred: datetime,
            index: int = index,
            line: str = line,
            base: dict = base,
            **fields,
        ) -> None:
            emit(
                {
                    "event_id": f"G-{index:05d}-{len(deliveries):06d}-{kind}",
                    "event_type": kind,
                    "source_id": f"{source}-{line}",
                    **base,
                    **fields,
                },
                occurred,
            )

        event("item_registered", "mes", moment, item_type_id="BODY-K1", origin="manufactured")
        event(
            "inspection_reported",
            "vision-inc",
            moment + timedelta(minutes=2),
            checkpoint_id="CP-INC-01",
            checkpoint_kind="incoming",
            inspection_result="no_defect_signs",
            observation_quality="good",
            confidence=0.95,
        )
        for run_id, operation, station, equipment, start_min, length in (
            (run_mill, "OP-MILL-010", "ST-MILL", "CNC-01", 5, 25),
            (run_weld, "OP-WELD-020", "ST-WELD", "WELD-01", 35, 20),
        ):
            event(
                "operation_started",
                f"term-{station.lower()}",
                moment + timedelta(minutes=start_min),
                operation_run_id=run_id,
                operation_id=operation,
                station_id=station,
                operator_id=f"OP-{rng.randint(101, 106)}",
                equipment_id=f"{equipment}-{line}",
            )
            event(
                "operation_finished",
                f"term-{station.lower()}",
                moment + timedelta(minutes=start_min + length),
                operation_run_id=run_id,
                outcome="completed",
            )
        defective = rng.random() < config.defect_rate
        event(
            "inspection_reported",
            "vision-weld",
            moment + timedelta(minutes=57),
            checkpoint_id="CP-WELD-01",
            checkpoint_kind="after_operation",
            station_id="ST-WELD",
            operation_run_id=run_weld,
            inspection_result="defect_signs_found" if defective else "no_defect_signs",
            defects=[{"defect_type": rng.choice(DEFECTS), "area": "шов 1", "severity": "major"}]
            if defective
            else None,
            observation_quality="good",
            confidence=0.9,
        )
    deliveries.sort(key=lambda delivery: delivery.received_at)
    return deliveries, unique
