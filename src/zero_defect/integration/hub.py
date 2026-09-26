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
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from zero_defect.integration.model import ExternalSystem, IntegrationError, QualityResult
from zero_defect.service import QualitySystem
from zero_defect.storage.database import Database
from zero_defect.storage.database import id_map as id_map_table
from zero_defect.storage.database import outbox as outbox_table

# Статусы изделия, по которым итог уже можно сообщать наружу.
REPORTABLE = ("conforming", "nonconforming")

# Пауза перед повтором растёт вдвое с каждой неудачей, но не больше часа.
BASE_BACKOFF_S = 5
MAX_BACKOFF_S = 3600
# После аварии отправителя сообщение снова становится доступным. Номер попытки
# служит версией захвата и не даёт запоздалому отправителю затереть новый ответ.
SEND_LEASE_S = 300


class Outbox:
    """Исходящая очередь и таблица сопоставления идентификаторов.

    Это изменяемое рабочее состояние, а не доказательство: доказательством служат
    записи integration_exchange в журнале. Поэтому очередь лежит в отдельных таблицах
    той же базы и может меняться.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def _insert(self, table):
        dialect = postgres_insert if self.database.is_postgres else sqlite_insert
        return dialect(table)

    def enqueue(self, system: str, result: QualityResult) -> bool:
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(result.__dict__, ensure_ascii=False, default=list)
        statement = (
            self._insert(outbox_table)
            .values(
                message_id=result.message_id,
                system=system,
                item_id=result.item_id,
                payload=payload,
                status="pending",
                attempts=0,
                next_attempt=now,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["message_id"])
        )
        with self.database.engine.begin() as conn:
            return conn.execute(statement).rowcount == 1

    def due(self, system: str, moment: datetime) -> list[tuple[str, dict, int]]:
        """Атомарно забрать готовые сообщения, включая просроченные захваты."""

        c = outbox_table.c
        available = and_(
            c.next_attempt <= moment.isoformat(),
            or_(c.status == "pending", c.status == "sending"),
        )
        with self.database.engine.connect() as conn:
            rows = conn.execute(
                select(c.message_id, c.payload, c.attempts)
                .where(c.system == system, available)
                .order_by(c.created_at)
            ).all()
        claimed = []
        lease_until = (moment + timedelta(seconds=SEND_LEASE_S)).isoformat()
        for message_id, payload, attempts in rows:
            # UPDATE с условиями выполняется в транзакции: конкурирующий процесс
            # увидит rowcount=0, даже если оба прочитали одну строку выше.
            with self.database.engine.begin() as conn:
                result = conn.execute(
                    update(outbox_table)
                    .where(
                        c.message_id == message_id,
                        c.system == system,
                        c.attempts == attempts,
                        available,
                    )
                    .values(status="sending", attempts=attempts + 1, next_attempt=lease_until)
                )
            if result.rowcount == 1:
                claimed.append((message_id, json.loads(payload), attempts + 1))
        return claimed

    def mark(
        self,
        message_id: str,
        status: str,
        attempts: int,
        error: str | None,
        next_attempt: datetime,
        ref: str | None,
    ) -> bool:
        with self.database.engine.begin() as conn:
            result = conn.execute(
                update(outbox_table)
                .where(
                    outbox_table.c.message_id == message_id,
                    outbox_table.c.status == "sending",
                    outbox_table.c.attempts == attempts,
                )
                .values(
                    status=status,
                    attempts=attempts,
                    last_error=error,
                    next_attempt=next_attempt.isoformat(),
                    external_ref=ref,
                )
            )
        return result.rowcount == 1

    def rows(self) -> list[dict]:
        c = outbox_table.c
        with self.database.engine.connect() as conn:
            result = conn.execute(
                select(
                    c.message_id,
                    c.system,
                    c.item_id,
                    c.status,
                    c.attempts,
                    c.last_error,
                    c.next_attempt,
                    c.external_ref,
                ).order_by(c.created_at)
            ).mappings()
            return [dict(row) for row in result]

    def map_id(self, system: str, entity: str, external_id: str, internal_id: str) -> None:
        statement = (
            self._insert(id_map_table)
            .values(system=system, entity=entity, external_id=external_id, internal_id=internal_id)
            .on_conflict_do_update(
                index_elements=["system", "entity", "external_id"],
                set_={"internal_id": internal_id},
            )
        )
        with self.database.engine.begin() as conn:
            conn.execute(statement)

    def id_map(self) -> list[dict]:
        with self.database.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(id_map_table)).mappings()]


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
        pending_ids = []
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
                pending_ids.append(planned.item_id)
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
        # Сопоставление означает, что факт действительно попал в журнал. Если
        # запись сорвалась или событие попало в карантин, следующая выгрузка
        # должна снова попытаться зарегистрировать изделие.
        for item_id, result in zip(pending_ids, results, strict=True):
            if result.status in ("accepted", "duplicate"):
                self.outbox.map_id(name, "item", item_id, item_id)
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
            if ack.accepted:
                status = "delivered"
                next_attempt = moment
            elif (
                ack.retryable
                and attempts < self.system.settings.adapters.get(name, _Default).max_attempts
            ):
                status = "pending"
                delay = min(BASE_BACKOFF_S * 2 ** (attempts - 1), MAX_BACKOFF_S)
                next_attempt = moment + timedelta(seconds=delay)
            else:
                status = "dead_letter"
                next_attempt = moment
            error = (
                None if ack.accepted else f"{ack.error_code}: {ack.error_message or ''}".strip(": ")
            )
            saved = self.outbox.mark(
                message_id, status, attempts, error, next_attempt, ack.external_ref
            )
            if saved:
                delivered += status == "delivered"
                retry += status == "pending"
                failed += status == "dead_letter"
            self.system.record_exchange(
                {
                    "system": name,
                    "direction": "outbound",
                    "message_id": message_id,
                    "item_id": result.item_id,
                    "verdict": result.verdict,
                    "attempt": attempts,
                    "status": status if saved else "stale_lease",
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
