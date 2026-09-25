"""
Воспроизведение сценария (Simulation): шаги подаются в систему в порядке поступления.

Один и тот же сценарий можно проиграть в процессе — так работают проверки и тесты — или
отправить в работающий сервис по HTTP с подписью пакета, как это делал бы edge-агент
(scripts/replay.py).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from zero_defect.ingest.pipeline import Delivery, DeliveryResult
from zero_defect.ledger.store import _SCHEMA
from zero_defect.service import QualitySystem


def load_steps(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def write_steps(path: Path, steps: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(step, ensure_ascii=False, sort_keys=True) for step in steps]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def tamper_event_record(db_path: Path, event_id: str) -> int:
    """Меняет один байт зашифрованной записи события в обход приложения.

    Так поступил бы человек с доступом к файлу базы: триггеры он снимает, правит запись и
    возвращает триггеры на место. Предотвратить это приложение не может — может только
    обнаружить, и именно это проверяет сценарий.
    """

    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        seq, ciphertext = conn.execute(
            "SELECT seq, ciphertext FROM ledger WHERE kind = 'source_event' AND ref_id = ?",
            (event_id,),
        ).fetchone()
        changed = bytearray(ciphertext)
        changed[len(changed) // 2] ^= 0x01
        conn.execute("DROP TRIGGER ledger_no_update")
        conn.execute("UPDATE ledger SET ciphertext = ? WHERE seq = ?", (bytes(changed), seq))
        conn.executescript(_SCHEMA)
        return seq
    finally:
        conn.close()


def update_is_prevented(db_path: Path) -> bool:
    """Пытается изменить запись обычным путём; True, если журнал это запретил."""

    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("UPDATE ledger SET kind = kind WHERE seq = 1")
        return False
    except sqlite3.DatabaseError:
        return True
    finally:
        conn.close()


def replay(system: QualitySystem, steps: list[dict]) -> dict:
    """Проигрывает шаги и возвращает сводку того, что произошло по ходу."""

    results: list[DeliveryResult] = []
    tampered: list[int] = []
    decision_errors: list[str] = []
    for step in steps:
        kind = step["kind"]
        if kind == "deliver":
            received = datetime.fromisoformat(step["received_at"])
            results.extend(system.ingest_deliveries([Delivery(step["event"], received)]))
        elif kind == "decide":
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
            tampered.append(tamper_event_record(system.settings.storage_path, step["event_id"]))
    return {"results": results, "tampered_seqs": tampered, "decision_errors": decision_errors}
