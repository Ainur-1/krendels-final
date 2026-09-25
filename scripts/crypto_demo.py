"""
Готовит набор для проверки долгосрочной защиты (раздел 6.3 постановки) и проверяет его.

В отдельном каталоге создаются записи до и после смены ключа и до и после смены
профиля, затем один пакет изменяется в обход приложения, а секретная часть первого
ключа удаляется. Итог печатается: что читается, что проверяется, что обнаружено.

    uv run python scripts/crypto_demo.py var/crypto-demo
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from zero_defect.config import PROJECT_ROOT
from zero_defect.ledger.crypto import Keyring
from zero_defect.ledger.store import Ledger
from zero_defect.simulation.replay import tamper_event_record


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else PROJECT_ROOT / "var" / "crypto-demo"
    shutil.rmtree(root, ignore_errors=True)
    keyring = Keyring(root / "keys")
    keyring.ensure("classic-v1")
    ledger = Ledger(root / "ledger.sqlite3", keyring)
    ledger.append("source_event", {"stage": "до смены ключа"}, ref_id="A1")
    first_key = keyring.active().key_id
    keyring.rotate()
    ledger.append("source_event", {"stage": "после смены ключа"}, ref_id="A2")
    keyring.rotate("hybrid-pq-v1")
    ledger.append("source_event", {"stage": "после смены профиля"}, ref_id="A3")
    ledger.append("source_event", {"stage": "пакет, который будет изменён"}, ref_id="A4")
    print("Записи и их защита:")
    for record in ledger.records():
        header = record.header
        print(
            f"  №{header.seq} {header.profile_id:13} {header.key_id} v{header.key_version}"
            f"  {header.mechanism}"
        )
    print(f"Проверка до вмешательства: ok = {ledger.verify().ok}")
    tamper_event_record(ledger.path, "A4")
    keyring.forget_secret(first_key)
    print(f"Секретная часть {first_key} удалена, запись A4 изменена в обход приложения.")
    for record in ledger.records():
        print(f"  №{record.header.seq}: {record.status}")
    report = ledger.verify()
    print(f"Проверка после: ok = {report.ok}")
    for problem in report.problems:
        print(f"  запись №{problem['seq']}: {problem['problem']}")
    ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
