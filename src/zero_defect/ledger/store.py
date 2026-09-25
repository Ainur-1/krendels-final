"""
Журнал: единственное место, где хранятся исходные события и решения людей.

Записи только дописываются. Исправление или решение контролёра — это новая запись со
ссылкой на прежнюю, а не правка старой. Изменяемые представления (история изделия,
карточки, показатели) строятся из журнала и в любой момент пересобираются заново.

Защита в три слоя, и каждый ловит своё:
- триггеры SQLite запрещают UPDATE и DELETE — это предотвращение для всех, кто
  работает через обычное соединение;
- каждая запись несёт хеш предыдущей, а последняя запись каждого пакета — подпись своего
  хеша. Через цепочку эта подпись удостоверяет и все записи до неё: правка любой
  записи в обход триггеров меняет её хеш, а с ним и хеш подписанной записи. Правка
  файла, вставка, удаление или перестановка записи обнаруживаются проверкой;
- якорь — подписанная голова журнала, хранящаяся отдельно от базы вместе с ключами, —
  ловит отрезание хвоста, которое цепочка сама по себе не видит.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from zero_defect.config import RECORD_FORMAT_VERSION
from zero_defect.ledger.crypto import PROFILES, CryptoError, Keyring

GENESIS = b"\x00" * 32

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq            INTEGER PRIMARY KEY,
    record_id      TEXT    NOT NULL UNIQUE,
    kind           TEXT    NOT NULL,
    ref_id         TEXT,
    written_at     TEXT    NOT NULL,
    format_version TEXT    NOT NULL,
    profile_id     TEXT    NOT NULL,
    key_id         TEXT    NOT NULL,
    key_version    INTEGER NOT NULL,
    mechanism      TEXT    NOT NULL,
    nonce          BLOB    NOT NULL,
    ciphertext     BLOB    NOT NULL,
    prev_hash      BLOB    NOT NULL,
    record_hash    BLOB    NOT NULL,
    signature      BLOB    NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_kind ON ledger(kind);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
"""

# Виды записей. Исходные события, отклонённые сообщения и повторные доставки хранятся
# раздельно: карантин не должен влиять на историю, а повтор — на показатели.
KINDS = (
    "source_event",
    "rejected_event",
    "duplicate_delivery",
    "decision",
    "critical_action",
    "integration_exchange",
)


def canonical(data: dict) -> bytes:
    """Однозначная сериализация: одинаковые данные всегда дают одинаковые байты."""

    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@dataclass(frozen=True)
class Header:
    """Открытая часть записи: всё, что нужно, чтобы выбрать механизм проверки."""

    seq: int
    record_id: str
    kind: str
    ref_id: str | None
    written_at: str
    format_version: str
    profile_id: str
    key_id: str
    key_version: int
    mechanism: str

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "record_id": self.record_id,
            "kind": self.kind,
            "ref_id": self.ref_id,
            "written_at": self.written_at,
            "format_version": self.format_version,
            "profile_id": self.profile_id,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "mechanism": self.mechanism,
        }


@dataclass(frozen=True)
class Record:
    """Прочитанная запись. payload пуст, если ключ недоступен или запись повреждена."""

    header: Header
    payload: dict | None
    status: str


@dataclass
class IntegrityReport:
    """Итог проверки журнала."""

    ok: bool
    checked: int
    problems: list[dict] = field(default_factory=list)
    head_seq: int = 0
    anchor_seq: int | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "problems": self.problems,
            "head_seq": self.head_seq,
            "anchor_seq": self.anchor_seq,
        }


class Ledger:
    """Журнал поверх SQLite с одной пишущей стороной."""

    def __init__(self, path: Path, keyring: Keyring) -> None:
        self.path = path
        self.keyring = keyring
        self._anchor_path = keyring.keys_dir / "anchor.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Один писатель: номер записи и хеш предыдущей должны браться атомарно, иначе две
        # параллельные записи сошлись бы на одном предшественнике и цепочка разветвилась.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def _head(self) -> tuple[int, bytes]:
        row = self._conn.execute(
            "SELECT seq, record_hash FROM ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return (row[0], row[1]) if row else (0, GENESIS)

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]

    def append(self, kind: str, payload: dict, ref_id: str | None = None) -> Header:
        return self.append_many([(kind, payload, ref_id)])[0]

    def append_many(self, entries: list[tuple[str, dict, str | None]]) -> list[Header]:
        """Дописывает записи одной транзакцией и обновляет якорь один раз на пакет."""

        if not entries:
            return []
        key = self.keyring.active()
        profile = PROFILES[key.profile_id]
        secret = self.keyring.secret(key.key_id)
        headers = []
        with self._lock:
            seq, prev_hash = self._head()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for kind, payload, ref_id in entries:
                    if kind not in KINDS:
                        raise ValueError(f"неизвестный вид записи {kind!r}")
                    seq += 1
                    header = Header(
                        seq=seq,
                        record_id=str(uuid.uuid4()),
                        kind=kind,
                        ref_id=ref_id,
                        written_at=datetime.now(UTC).isoformat(),
                        format_version=RECORD_FORMAT_VERSION,
                        profile_id=profile.profile_id,
                        key_id=key.key_id,
                        key_version=key.key_version,
                        mechanism=profile.mechanism,
                    )
                    aad = canonical(header.as_dict())
                    nonce, ciphertext = profile.encrypt(secret, canonical(payload), aad)
                    record_hash = profile.digest(prev_hash + aad + nonce + ciphertext)
                    # Подписывается только последняя запись пакета. Подпись ML-DSA стоит
                    # около 4,5 мс, и подпись каждой записи съедала 98 % времени приёма
                    # (профиль нагрузочного прогона); через цепочку хешей подпись
                    # последней записи удостоверяет весь пакет.
                    last = len(headers) == len(entries) - 1
                    signature = profile.sign(secret, record_hash) if last else b""
                    self._conn.execute(
                        "INSERT INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            header.seq,
                            header.record_id,
                            header.kind,
                            header.ref_id,
                            header.written_at,
                            header.format_version,
                            header.profile_id,
                            header.key_id,
                            header.key_version,
                            header.mechanism,
                            nonce,
                            ciphertext,
                            prev_hash,
                            record_hash,
                            signature,
                        ),
                    )
                    prev_hash = record_hash
                    headers.append(header)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._write_anchor(seq, prev_hash)
        return headers

    def _write_anchor(self, seq: int, record_hash: bytes) -> None:
        key = self.keyring.active()
        profile = PROFILES[key.profile_id]
        message = canonical({"seq": seq, "hash": record_hash.hex()})
        anchor = {
            "seq": seq,
            "hash": record_hash.hex(),
            "key_id": key.key_id,
            "signature": profile.sign(self.keyring.secret(key.key_id), message).hex(),
        }
        self._anchor_path.parent.mkdir(parents=True, exist_ok=True)
        self._anchor_path.write_text(json.dumps(anchor), encoding="utf-8")

    def _rows(self, kinds: tuple[str, ...] | None = None) -> Iterator[tuple]:
        # Двоичные столбцы приводятся к BLOB явно: если в обход приложения туда записали
        # текст, чтение не падает целиком, а проверка указывает на испорченную запись.
        query = (
            "SELECT seq, record_id, kind, ref_id, written_at, format_version, profile_id, key_id,"
            " key_version, mechanism, CAST(nonce AS BLOB), CAST(ciphertext AS BLOB),"
            " CAST(prev_hash AS BLOB), CAST(record_hash AS BLOB), CAST(signature AS BLOB)"
            " FROM ledger"
        )
        params: tuple = ()
        if kinds:
            query += f" WHERE kind IN ({','.join('?' * len(kinds))})"
            params = kinds
        yield from self._conn.execute(query + " ORDER BY seq", params)

    def records(self, kinds: tuple[str, ...] | None = None) -> Iterator[Record]:
        """Расшифрованные записи по порядку. Сбой одной записи не прерывает чтение."""

        for row in self._rows(kinds):
            header = Header(*row[:10])
            nonce, ciphertext = row[10], row[11]
            profile = PROFILES.get(header.profile_id)
            if profile is None:
                yield Record(header, None, "unknown_profile")
                continue
            if not self.keyring.has_secret(header.key_id):
                yield Record(header, None, "key_unavailable")
                continue
            try:
                plain = profile.decrypt(
                    self.keyring.secret(header.key_id),
                    nonce,
                    ciphertext,
                    canonical(header.as_dict()),
                )
            except CryptoError:
                yield Record(header, None, "decrypt_failed")
                continue
            yield Record(header, json.loads(plain), "ok")

    def verify(self) -> IntegrityReport:
        """Проверяет цепочку, подписи и якорь. Не требует секретных ключей."""

        problems: list[dict] = []
        prev_hash = GENESIS
        expected_seq = 0
        checked = 0
        hashes: dict[int, bytes] = {}
        last_signed = False
        for row in self._rows():
            checked += 1
            header = Header(*row[:10])
            nonce, ciphertext, stored_prev, record_hash, signature = row[10:15]
            expected_seq += 1
            where = {"seq": header.seq, "record_id": header.record_id, "kind": header.kind}
            if header.seq != expected_seq:
                problems.append({**where, "problem": "sequence_gap"})
                expected_seq = header.seq
            if stored_prev != prev_hash:
                problems.append({**where, "problem": "chain_break"})
            profile = PROFILES.get(header.profile_id)
            try:
                key = self.keyring.info(header.key_id)
            except CryptoError:
                key = None
            if profile is None or key is None:
                problems.append({**where, "problem": "unknown_key"})
            else:
                aad = canonical(header.as_dict())
                if profile.digest(stored_prev + aad + nonce + ciphertext) != record_hash:
                    problems.append({**where, "problem": "hash_mismatch"})
                if signature and not profile.verify(key.public, record_hash, signature):
                    problems.append({**where, "problem": "signature_invalid"})
            last_signed = bool(signature)
            hashes[header.seq] = record_hash
            prev_hash = record_hash
        if checked and not last_signed:
            # Каждый пакет заканчивается подписанной записью. Неподписанный хвост значит,
            # что записи дописаны мимо приложения.
            problems.append({"seq": expected_seq, "problem": "unsigned_tail"})
        anchor_seq = None
        if self._anchor_path.exists():
            anchor = json.loads(self._anchor_path.read_text(encoding="utf-8"))
            anchor_seq = anchor["seq"]
            try:
                key = self.keyring.info(anchor["key_id"])
                profile = PROFILES[key.profile_id]
                message = canonical({"seq": anchor["seq"], "hash": anchor["hash"]})
                if not profile.verify(key.public, message, bytes.fromhex(anchor["signature"])):
                    problems.append({"seq": anchor_seq, "problem": "anchor_signature_invalid"})
            except CryptoError:
                problems.append({"seq": anchor_seq, "problem": "anchor_unknown_key"})
            if anchor_seq > expected_seq:
                problems.append({"seq": anchor_seq, "problem": "tail_truncated"})
            elif anchor_seq in hashes and hashes[anchor_seq].hex() != anchor["hash"]:
                problems.append({"seq": anchor_seq, "problem": "anchor_mismatch"})
        return IntegrityReport(
            ok=not problems,
            checked=checked,
            problems=problems,
            head_seq=expected_seq,
            anchor_seq=anchor_seq,
        )
