"""
Журнал: только дописывание, обнаружение вмешательства, смена ключей и профилей.

Шифрование, подлинность и неизменность проверяются раздельно, как требует постановка:
запись, которую нельзя расшифровать, всё равно проверяема по подписи, а запись,
которую удалось расшифровать, всё равно может оказаться подменённой в цепочке. Каждый
тест идёт на обеих СУБД — SQLite и PostgreSQL: защита от изменения в них устроена
по-разному, а обнаружение обязано работать одинаково.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from zero_defect.ledger.crypto import PROFILES, Keyring
from zero_defect.ledger.store import Header, Ledger, canonical
from zero_defect.storage.database import Database, create_guard


@pytest.fixture()
def ledger(database_url, tmp_path):
    database = Database(database_url)
    keyring = Keyring(tmp_path / "keys")
    keyring.ensure("hybrid-pq-v1")
    yield Ledger(database, keyring)
    database.dispose()


def bypass(ledger: Ledger, sql: str, params: dict | None = None) -> None:
    """Изменение в обход защиты — так действовал бы владелец базы."""

    database = ledger.database
    with database.engine.begin() as conn:
        if database.is_postgres:
            conn.execute(text("ALTER TABLE ledger DISABLE TRIGGER USER"))
        else:
            conn.execute(text("DROP TRIGGER ledger_no_update"))
            conn.execute(text("DROP TRIGGER ledger_no_delete"))
        conn.execute(text(sql), params or {})
        if database.is_postgres:
            conn.execute(text("ALTER TABLE ledger ENABLE TRIGGER USER"))
    create_guard(database)


def fill(ledger: Ledger, count: int = 5) -> None:
    for index in range(count):
        ledger.append("source_event", {"n": index}, ref_id=f"E{index}")


def test_payload_is_encrypted_at_rest(ledger):
    ledger.append("source_event", {"secret": "поры в корне шва"})
    with ledger.database.engine.connect() as conn:
        stored = conn.execute(text("SELECT ciphertext FROM ledger")).scalar_one()
    assert "поры".encode() not in bytes(stored)
    assert next(ledger.records()).payload == {"secret": "поры в корне шва"}


@pytest.mark.parametrize(
    "sql", ["UPDATE ledger SET kind = 'x' WHERE seq = 1", "DELETE FROM ledger WHERE seq = 1"]
)
def test_update_and_delete_are_prevented(ledger, sql):
    fill(ledger)
    with pytest.raises(DBAPIError), ledger.database.engine.begin() as conn:
        conn.execute(text(sql))
    assert ledger.verify().ok


def test_clean_ledger_verifies(ledger):
    fill(ledger)
    report = ledger.verify()
    assert report.ok and report.checked == 5 and report.anchor_seq == 5


@pytest.mark.parametrize(
    ("sql", "problem"),
    [
        ("UPDATE ledger SET ciphertext = :forged WHERE seq = 2", "hash_mismatch"),
        ("DELETE FROM ledger WHERE seq = 3", "chain_break"),
        ("UPDATE ledger SET kind = 'decision' WHERE seq = 2", "hash_mismatch"),
    ],
)
def test_bypass_changes_are_detected(ledger, sql, problem):
    fill(ledger)
    bypass(ledger, sql, {"forged": b"\x00" * 40} if ":forged" in sql else None)
    problems = {item["problem"] for item in ledger.verify().problems}
    assert problem in problems


def test_tail_truncation_is_caught_by_anchor(ledger):
    fill(ledger)
    bypass(ledger, "DELETE FROM ledger WHERE seq = 5")
    assert "tail_truncated" in {item["problem"] for item in ledger.verify().problems}


def test_rewriting_the_whole_chain_breaks_the_signature(ledger):
    """Злоумышленник пересчитал хеши после правки — подпись пакета его выдаёт."""

    fill(ledger, 1)
    profile = PROFILES["hybrid-pq-v1"]
    row = next(ledger._rows())
    forged_cipher = b"\x00" + row[11][1:]
    aad = canonical(Header(*row[:10]).as_dict())
    forged_hash = profile.digest(row[12] + aad + row[10] + forged_cipher)
    bypass(
        ledger,
        "UPDATE ledger SET ciphertext = :c, record_hash = :h WHERE seq = 1",
        {"c": forged_cipher, "h": forged_hash},
    )
    problems = {item["problem"] for item in ledger.verify().problems}
    assert "signature_invalid" in problems


def test_rotation_and_profile_change_keep_old_records_verifiable(ledger):
    fill(ledger, 2)
    ledger.keyring.rotate("classic-v1")
    fill(ledger, 2)
    ledger.keyring.rotate("hybrid-pq-v1")
    fill(ledger, 1)
    profiles = [record.header.profile_id for record in ledger.records()]
    assert profiles == ["hybrid-pq-v1"] * 2 + ["classic-v1"] * 2 + ["hybrid-pq-v1"]
    assert ledger.verify().ok
    assert all(record.status == "ok" for record in ledger.records())


def test_unavailable_key_blocks_reading_but_not_verification(ledger):
    fill(ledger, 2)
    old = ledger.keyring.active().key_id
    ledger.keyring.rotate()
    fill(ledger, 1)
    ledger.keyring.forget_secret(old)
    statuses = [record.status for record in ledger.records()]
    assert statuses == ["key_unavailable", "key_unavailable", "ok"]
    assert ledger.verify().ok


def test_hybrid_signature_needs_both_halves():
    profile = PROFILES["hybrid-pq-v1"]
    secret = profile.new_secret()
    public = profile.public_of(secret)
    signature = profile.sign(secret, b"head")
    assert profile.verify(public, b"head", signature)
    broken = signature[:-1] + bytes([signature[-1] ^ 1])
    assert not profile.verify(public, b"head", broken)
    classic_only = signature[: 2 + 64]
    assert not profile.verify(public, b"head", classic_only)


def test_secret_files_are_owner_only(tmp_path):
    keyring = Keyring(tmp_path / "keys")
    keyring.ensure("classic-v1")
    mode = (tmp_path / "keys" / "keyring.json").stat().st_mode & 0o777
    assert mode == 0o600
