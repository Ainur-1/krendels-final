"""
База данных: схема, подключение и защита журнала средствами самой СУБД.

Основная СУБД — PostgreSQL: журнал, исходящая очередь, пользователи и конфигурации
линий лежат в одной базе, к которой можно подключить резервирование, реплику для
чтения и права ролей на уровне СУБД. SQLite поддерживается тем же кодом — на нём идут
быстрые тесты. Всё, что зависит от диалекта, собрано здесь: остальной код работает с
таблицами SQLAlchemy и не знает, какая СУБД под ним.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Engine,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    text,
)

metadata = MetaData()

ledger = Table(
    "ledger",
    metadata,
    Column("seq", BigInteger, primary_key=True, autoincrement=False),
    Column("record_id", String(36), nullable=False, unique=True),
    Column("kind", String(32), nullable=False, index=True),
    Column("ref_id", String(256)),
    Column("written_at", String(40), nullable=False),
    Column("format_version", String(32), nullable=False),
    Column("profile_id", String(64), nullable=False),
    Column("key_id", String(64), nullable=False),
    Column("key_version", Integer, nullable=False),
    Column("mechanism", String(160), nullable=False),
    Column("nonce", LargeBinary, nullable=False),
    Column("ciphertext", LargeBinary, nullable=False),
    Column("prev_hash", LargeBinary, nullable=False),
    Column("record_hash", LargeBinary, nullable=False),
    Column("signature", LargeBinary, nullable=False),
)

outbox = Table(
    "outbox",
    metadata,
    Column("message_id", String(64), primary_key=True),
    Column("system", String(64), nullable=False),
    Column("item_id", String(128), nullable=False),
    Column("payload", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", Text),
    Column("next_attempt", String(40), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("external_ref", String(128)),
)

id_map = Table(
    "id_map",
    metadata,
    Column("system", String(64), primary_key=True),
    Column("entity", String(64), primary_key=True),
    Column("external_id", String(256), primary_key=True),
    Column("internal_id", String(256), nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("user_id", String(64), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("role", String(32), nullable=False),
    # scrypt; пусто — вход только в демонстрационном режиме.
    Column("password_hash", String(256)),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", String(40), nullable=False),
)

lines = Table(
    "lines",
    metadata,
    Column("line_id", String(64), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("config", Text, nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("updated_by", String(64), nullable=False),
)

# Номер рекомендательной блокировки PostgreSQL, под которой пишется журнал. Номер записи и
# хеш предыдущей берутся атомарно и между процессами: два экземпляра сервиса на одной
# базе не разветвят цепочку.
LEDGER_LOCK_KEY = 7_140_311

_GUARD_SQLITE = [
    "CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger "
    "BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger "
    "BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END",
]

_GUARD_POSTGRES = [
    "CREATE OR REPLACE FUNCTION zd_ledger_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'ledger is append-only'; END; $$ LANGUAGE plpgsql",
    "CREATE OR REPLACE TRIGGER ledger_append_only BEFORE UPDATE OR DELETE ON ledger "
    "FOR EACH ROW EXECUTE FUNCTION zd_ledger_append_only()",
    "CREATE OR REPLACE TRIGGER ledger_no_truncate BEFORE TRUNCATE ON ledger "
    "FOR EACH STATEMENT EXECUTE FUNCTION zd_ledger_append_only()",
]


class Database:
    """Подключение к базе и то, что зависит от её диалекта."""

    def __init__(self, url: str) -> None:
        self.url = url
        if url.startswith("sqlite"):
            path = url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.engine: Engine = create_engine(url, connect_args={"check_same_thread": False})
            event.listen(self.engine, "connect", _sqlite_pragmas)
        else:
            self.engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        self.dialect = self.engine.dialect.name
        # Внутри одного процесса запись журнала сериализуется ещё и здесь: для SQLite
        # это единственная защита, для PostgreSQL — дешёвая первая ступень перед
        # блокировкой в базе.
        self.write_lock = threading.Lock()
        self.create_schema()

    @property
    def is_postgres(self) -> bool:
        return self.dialect == "postgresql"

    def create_schema(self) -> None:
        metadata.create_all(self.engine)
        create_guard(self)

    @contextmanager
    def ledger_transaction(self):
        """Транзакция записи журнала под блокировкой одного писателя."""

        with self.write_lock, self.engine.begin() as conn:
            if self.is_postgres:
                conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LEDGER_LOCK_KEY})
            yield conn

    def dispose(self) -> None:
        self.engine.dispose()


def create_guard(database: Database) -> None:
    """Защита журнала от изменения средствами СУБД; повторный вызов безопасен."""

    with database.engine.begin() as conn:
        for statement in _GUARD_POSTGRES if database.is_postgres else _GUARD_SQLITE:
            conn.execute(text(statement))


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


_shared: dict[str, Database] = {}
_shared_lock = threading.Lock()


def open_database(url: str) -> Database:
    """Одна Database на адрес в пределах процесса: у журнала должен быть один писатель."""

    with _shared_lock:
        database = _shared.get(url)
        if database is None:
            database = Database(url)
            _shared[url] = database
        return database


def close_database(url: str) -> None:
    with _shared_lock:
        database = _shared.pop(url, None)
    if database is not None:
        database.dispose()
