"""
Связующее звено между ядром и внешними системами.

Входящее направление: задания из внешней системы превращаются в обычные события
item_registered и идут через тот же приём, что и события линии. Идентификатор события
вычисляется из задания, поэтому повторная выгрузка того же задания — повтор, а не
второе изделие.

Исходящее направление: итог по изделию кладётся в исходящую очередь (outbox) и только
потом отправляется. Очередь переживает недоступность внешней системы: временная
ошибка оставляет сообщение в очереди с растущей паузой до следующей попытки, ошибка
содержания уводит его в недоставленные с причиной. Каждая попытка пишется в журнал.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from zero_defect.integration.model import ExternalSystem, IntegrationError, QualityResult
from zero_defect.service import QualitySystem

# Статусы изделия, по которым итог уже можно сообщать наружу.
REPORTABLE = ("conforming", "nonconforming")

# Пауза перед повтором растёт вдвое с каждой неудачей, но не больше часа.
BASE_BACKOFF_S = 5
MAX_BACKOFF_S = 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    message_id   TEXT PRIMARY KEY,
    system       TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    next_attempt TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    external_ref TEXT
);
CREATE TABLE IF NOT EXISTS id_map (
    system      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    internal_id TEXT NOT NULL,
    PRIMARY KEY (system, entity, external_id)
);
"""


class Outbox:
    """Исходящая очередь и таблица сопоставления идентификаторов.

    Это изменяемое рабочее состояние, а не доказательство: доказательством служат
    записи integration_exchange в журнале. Поэтому очередь лежит в отдельных таблицах
    и может меняться.
    """

    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.executescript(_SCHEMA)

    def enqueue(self, system: str, result: QualityResult) -> bool:
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(result.__dict__, ensure_ascii=False, default=list)
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO outbox (message_id, system, item_id, payload, status,"
                " next_attempt, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (result.message_id, system, result.item_id, payload, now, now),
            )
            return cursor.rowcount == 1

    def due(self, system: str, moment: datetime) -> list[tuple[str, dict, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT message_id, payload, attempts FROM outbox WHERE system = ?"
                " AND status = 'pending' AND next_attempt <= ? ORDER BY created_at",
                (system, moment.isoformat()),
            ).fetchall()
        return [(row[0], json.loads(row[1]), row[2]) for row in rows]

    def mark(
        self,
        message_id: str,
        status: str,
        attempts: int,
        error: str | None,
        next_attempt: datetime,
        ref: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET status = ?, attempts = ?, last_error = ?, next_attempt = ?,"
                " external_ref = ? WHERE message_id = ?",
                (status, attempts, error, next_attempt.isoformat(), ref, message_id),
            )

    def rows(self) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT message_id, system, item_id, status, attempts, last_error, next_attempt,"
                " external_ref FROM outbox ORDER BY created_at"
            )
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def map_id(self, system: str, entity: str, external_id: str, internal_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO id_map VALUES (?, ?, ?, ?)",
                (system, entity, external_id, internal_id),
            )

    def id_map(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT system, entity, external_id, internal_id FROM id_map"
            ).fetchall()
        return [
            {"system": r[0], "entity": r[1], "external_id": r[2], "internal_id": r[3]} for r in rows
        ]


def result_for(system: QualitySystem, item_id: str) -> QualityResult | None:
    """Итог по изделию из текущего состояния ядра, если его уже можно сообщать."""

    state = system.state()
    status = state.statuses.get(item_id)
    item = state.history.items.get(item_id)
    if status not in REPORTABLE or item is None:
        return None
    members = {item_id, *item.components}
    ncs = tuple(
        sorted(
            (
                {
                    "nc_id": card.nc_id,
                    "defect_type": card.defect_type,
                    "status": card.status,
                    "cause": card.confirmed_cause,
                }
                for card in state.cards.values()
                if card.item_id in members
            ),
            key=lambda entry: entry["nc_id"],
        )
    )
    fingerprint = json.dumps([item_id, status, ncs], sort_keys=True, ensure_ascii=False)
    message_id = "QR-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    return QualityResult(
        message_id=message_id,
        work_order_id=item.work_order_id,
        item_id=item_id,
        item_type_id=item.item_type_id,
        verdict=status,
        nonconformances=ncs,
        decided_at=datetime.now(UTC).isoformat(),
    )


class IntegrationHub:
    """Синхронизация с подключёнными внешними системами."""

    def __init__(
        self, system: QualitySystem, adapters: dict[str, ExternalSystem], outbox: Outbox
    ) -> None:
        self.system = system
        self.adapters = adapters
        self.outbox = outbox
        self.orders: dict[str, set[str]] = {name: set() for name in adapters}
        self.last_errors: dict[str, str | None] = {name: None for name in adapters}

    def known_orders(self, name: str) -> set[str]:
        """Задания системы: из текущей выгрузки и из сопоставления, пережившего перезапуск."""

        mapped = {
            row["internal_id"]
            for row in self.outbox.id_map()
            if row["system"] == name and row["entity"] == "work_order"
        }
        return self.orders[name] | mapped

    def pull(self, name: str) -> dict:
        adapter = self.adapters[name]
        orders = adapter.fetch_work_orders()
        adapter.fetch_reference()
        messages = []
        received = datetime.now(UTC)
        # Изделие, уже сопоставленное с этой системой, повторно не регистрируется. Иначе
        # повторная выгрузка задания давала бы то же событие с другим временем, а такое
        # сообщение приём по праву считает конфликтом и отправляет в карантин.
        known = {
            row["external_id"]
            for row in self.outbox.id_map()
            if row["system"] == name and row["entity"] == "item"
        }
        already_known = 0
        for order in orders:
            self.orders[name].add(order.work_order_id)
            if order.external_id:
                self.outbox.map_id(name, "work_order", order.external_id, order.work_order_id)
            for planned in order.items:
                if planned.item_id in known:
                    already_known += 1
                    continue
                self.outbox.map_id(name, "item", planned.item_id, planned.item_id)
                messages.append(
                    {
                        "event_id": f"int-{name}-{order.work_order_id}-{planned.item_id}",
                        "event_type": "item_registered",
                        "schema_version": "1.0",
                        "occurred_at": received.isoformat(),
                        "source_id": f"int-{name}",
                        "item_id": planned.item_id,
                        "item_type_id": planned.item_type_id,
                        "line_id": order.line_id,
                        "work_order_id": order.work_order_id,
                        "origin": planned.origin,
                    }
                )
        results = self.system.ingest(messages, received_at=received)
        return {
            "orders": len(orders),
            "registered": sum(1 for result in results if result.status == "accepted"),
            "already_known": already_known
            + sum(1 for result in results if result.status == "duplicate"),
        }

    def enqueue_results(self, name: str) -> int:
        state = self.system.state()
        queued = 0
        for item_id, item in state.history.items.items():
            if item.parent_id is not None:
                continue
            # Наружу уходят только итоги по заданиям этой системы: чужое изделие она не
            # знает и отклонила бы как ошибку содержания.
            if item.work_order_id not in self.known_orders(name):
                continue
            result = result_for(self.system, item_id)
            if result is not None and self.outbox.enqueue(name, result):
                queued += 1
        return queued

    def flush(self, name: str, moment: datetime | None = None) -> dict:
        adapter = self.adapters[name]
        moment = moment or datetime.now(UTC)
        delivered = failed = retry = 0
        for message_id, payload, attempts in self.outbox.due(name, moment):
            result = QualityResult(
                **{**payload, "nonconformances": tuple(payload["nonconformances"])}
            )
            ack = adapter.send_result(result)
            attempts += 1
            if ack.accepted:
                status, delivered = "delivered", delivered + 1
                next_attempt = moment
            elif (
                ack.retryable
                and attempts < self.system.settings.adapters.get(name, _Default).max_attempts
            ):
                status, retry = "pending", retry + 1
                delay = min(BASE_BACKOFF_S * 2 ** (attempts - 1), MAX_BACKOFF_S)
                next_attempt = moment + timedelta(seconds=delay)
            else:
                status, failed = "dead_letter", failed + 1
                next_attempt = moment
            error = (
                None if ack.accepted else f"{ack.error_code}: {ack.error_message or ''}".strip(": ")
            )
            self.outbox.mark(message_id, status, attempts, error, next_attempt, ack.external_ref)
            self.system.record_exchange(
                {
                    "system": name,
                    "direction": "outbound",
                    "message_id": message_id,
                    "item_id": result.item_id,
                    "verdict": result.verdict,
                    "attempt": attempts,
                    "status": status,
                    "error": error,
                    "external_ref": ack.external_ref,
                    "at": moment.isoformat(),
                }
            )
        return {"delivered": delivered, "retry_later": retry, "dead_letter": failed}

    def sync(self, name: str | None = None) -> dict:
        report = {}
        for adapter_name in [name] if name else list(self.adapters):
            try:
                pulled = self.pull(adapter_name)
                self.last_errors[adapter_name] = None
            except IntegrationError as error:
                pulled = {"error": str(error), "retryable": error.retryable}
                self.last_errors[adapter_name] = str(error)
            queued = self.enqueue_results(adapter_name)
            report[adapter_name] = {
                "pull": pulled,
                "queued": queued,
                "push": self.flush(adapter_name),
            }
        return report


class _Default:
    max_attempts = 5
