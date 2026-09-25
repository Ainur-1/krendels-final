"""
Нагрузочный прогон: один и несколько обработчиков, недоступность и восстановление.

Главный вопрос не скорость, а смысл: история и показатели после прогона с четырьмя
обработчиками, после сбоя с повторной доставкой и после перемешанного порядка должны
совпадать с эталонным прогоном одним обработчиком до последнего знака. Совпадение
проверяется отпечатком — хешем показателей и статусов всех изделий.
"""

from __future__ import annotations

import hashlib
import json
import platform
import random
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from zero_defect.ingest.pipeline import Delivery
from zero_defect.service import QualitySystem
from zero_defect.simulation.checker import fresh_settings
from zero_defect.simulation.edge import EdgeAgent
from zero_defect.simulation.generator import GeneratorConfig, generate


def fingerprint(system: QualitySystem) -> str:
    state = system.state()
    payload = {
        "metrics": state.metrics,
        "statuses": dict(sorted(state.statuses.items())),
        "cards": sorted(
            (card.nc_id, card.status, len(card.signals)) for card in state.cards.values()
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _summary(system: QualitySystem, unique: int, seconds: float, messages: int) -> dict:
    ingest = system.ingest_summary()
    telemetry = system.telemetry.snapshot()["distributions"].get("ingest.batch_seconds", {})
    return {
        "messages_sent": messages,
        "unique_events": unique,
        "accepted": ingest["accepted"],
        "duplicates": ingest["duplicates"],
        "rejected": ingest["rejected"],
        "complete": ingest["accepted"] == unique,
        "seconds": round(seconds, 3),
        "messages_per_second": round(messages / seconds, 1) if seconds else None,
        "batch_p50_ms": round(telemetry.get("p50", 0) * 1000, 2),
        "batch_p95_ms": round(telemetry.get("p95", 0) * 1000, 2),
        "fingerprint": fingerprint(system),
    }


def run_direct(
    config: GeneratorConfig, workers: int, batch: int = 50, shuffle: bool = False
) -> dict:
    deliveries, unique = generate(config)
    if shuffle:
        random.Random(config.seed + 1).shuffle(deliveries)
    with tempfile.TemporaryDirectory() as tmp:
        system = QualitySystem(fresh_settings(Path(tmp), workers=workers))
        try:
            started = time.perf_counter()
            for offset in range(0, len(deliveries), batch):
                system.ingest_deliveries(deliveries[offset : offset + batch])
            elapsed = time.perf_counter() - started
            return {
                "workers": workers,
                "shuffled": shuffle,
                **_summary(system, unique, elapsed, len(deliveries)),
            }
        finally:
            system.close()


def run_outage(
    config: GeneratorConfig, workers: int, outage_batches: tuple[int, int] = (3, 9)
) -> dict:
    """Центр недоступен для пакетов с номерами из диапазона; один ответ теряется."""

    deliveries, unique = generate(config)
    with tempfile.TemporaryDirectory() as tmp:
        system = QualitySystem(fresh_settings(Path(tmp), workers=workers))
        key = system.keyring.register_source("edge-load")
        calls = {"n": 0, "refused": 0, "lost_responses": 0}

        def send(body: bytes, headers: dict[str, str]) -> int:
            calls["n"] += 1
            system.security.from_source(headers["X-Source-Id"], body, headers["X-Signature"])
            if outage_batches[0] <= calls["n"] < outage_batches[1]:
                calls["refused"] += 1
                return 503
            messages = json.loads(body)["events"]
            system.ingest_deliveries([Delivery(message, datetime.now(UTC)) for message in messages])
            # Первый пакет после восстановления принят, но ответ до агента не дошёл:
            # агент честно отправит его ещё раз.
            if calls["n"] == outage_batches[1]:
                calls["lost_responses"] += 1
                return 504
            return 202

        agent = EdgeAgent("edge-load", key, send)
        try:
            started = time.perf_counter()
            for offset in range(0, len(deliveries), agent.batch_size):
                agent.submit(
                    [delivery.raw for delivery in deliveries[offset : offset + agent.batch_size]]
                )
                agent.flush()
            while agent.buffer:
                agent.flush()
            elapsed = time.perf_counter() - started
            return {
                "workers": workers,
                "outage": {
                    "refused_batches": calls["refused"],
                    "lost_responses": calls["lost_responses"],
                    "agent_failed_attempts": agent.failed_attempts,
                },
                **_summary(system, unique, elapsed, len(deliveries)),
            }
        finally:
            system.close()


def run_all(config: GeneratorConfig) -> dict:
    baseline = run_direct(config, workers=1)
    runs = [
        baseline,
        run_direct(config, workers=4),
        run_direct(config, workers=1, shuffle=True),
        run_outage(config, workers=4),
    ]
    return {
        "generator": {key: str(value) for key, value in config.__dict__.items()},
        "machine": {"python": platform.python_version(), "platform": platform.platform()},
        "runs": runs,
        "same_result_everywhere": all(
            run["fingerprint"] == baseline["fingerprint"] for run in runs
        ),
        "no_double_counting": all(run["complete"] for run in runs),
    }
