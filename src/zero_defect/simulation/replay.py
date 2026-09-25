"""
Воспроизведение сценария (Simulation): шаги подаются в систему в порядке поступления.

Один и тот же сценарий можно проиграть в процессе — так работают проверки и тесты — или
отправить в работающий сервис по HTTP с подписью пакета, как это делал бы edge-агент
(scripts/replay.py).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from zero_defect.ingest.pipeline import Delivery, DeliveryResult
from zero_defect.service import QualitySystem
from zero_defect.simulation.tamper import tamper_event_record


def load_steps(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def write_steps(path: Path, steps: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(step, ensure_ascii=False, sort_keys=True) for step in steps]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def replay(system: QualitySystem, steps: list[dict]) -> dict:
    """Проигрывает шаги и возвращает сводку того, что произошло по ходу."""

    results: list[DeliveryResult] = []
    tampered: list[int] = []
    decision_errors: list[str] = []
    batch: list[Delivery] = []

    def flush() -> None:
        if batch:
            results.extend(system.ingest_deliveries(list(batch)))
            batch.clear()

    for step in steps:
        kind = step["kind"]
        if kind == "deliver":
            # Подряд идущие доставки уходят одним пакетом, как от edge-агента: так история
            # на тысячи событий загружается за секунды, а порядок шагов не меняется.
            batch.append(Delivery(step["event"], datetime.fromisoformat(step["received_at"])))
            if len(batch) >= 200:
                flush()
            continue
        flush()
        if kind == "decide":
            principal = system.security.user(step["user_id"])
            try:
                system.decide(
                    principal,
                    step["nc_id"],
                    step["action"],
                    step["reason"],
                    cause_category=step.get("cause_category"),
                    decided_at=datetime.fromisoformat(step["at"]),
                )
            except Exception as error:  # noqa: BLE001 — ошибка решения часть итога сценария
                decision_errors.append(f"{step['nc_id']} {step['action']}: {error}")
        elif kind == "tamper":
            tampered.append(tamper_event_record(system.database, step["event_id"]))
    flush()
    return {"results": results, "tampered_seqs": tampered, "decision_errors": decision_errors}
