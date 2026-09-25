"""
Контрольное изменение записи журнала в обход приложения — для сценария 08 и показа.

Так поступил бы человек с правами владельца базы: он снимает защиту от изменения,
правит запись и возвращает защиту на место. Предотвратить это приложение не может —
может только обнаружить, и именно это проверяется. Код намеренно лежит в
simulation/, а не в ядре: сервис сам такого делать не умеет.
"""

from __future__ import annotations

from sqlalchemy import LargeBinary, cast, select, text, update
from sqlalchemy.exc import DBAPIError

from zero_defect.storage.database import Database, create_guard
from zero_defect.storage.database import ledger as ledger_table


def tamper_event_record(database: Database, event_id: str) -> int:
    """Меняет один байт зашифрованной записи события; возвращает её номер."""

    c = ledger_table.c
    with database.engine.begin() as conn:
        seq, ciphertext = conn.execute(
            select(c.seq, cast(c.ciphertext, LargeBinary)).where(
                c.kind == "source_event", c.ref_id == event_id
            )
        ).one()
        changed = bytearray(ciphertext)
        changed[len(changed) // 2] ^= 0x01
        if database.is_postgres:
            conn.execute(text("ALTER TABLE ledger DISABLE TRIGGER ledger_append_only"))
        else:
            conn.execute(text("DROP TRIGGER ledger_no_update"))
        conn.execute(update(ledger_table).where(c.seq == seq).values(ciphertext=bytes(changed)))
        if database.is_postgres:
            conn.execute(text("ALTER TABLE ledger ENABLE TRIGGER ledger_append_only"))
    create_guard(database)
    return seq


def update_is_prevented(database: Database) -> bool:
    """Пытается изменить запись обычным путём; True, если СУБД это запретила."""

    try:
        with database.engine.begin() as conn:
            conn.execute(
                update(ledger_table).where(ledger_table.c.seq == 1).values(kind=ledger_table.c.kind)
            )
    except DBAPIError:
        return True
    return False
