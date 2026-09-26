"""
Приём событий: проверка, повторы, опоздания и запись в журнал.

Правила поставки — «хотя бы один раз»: источник вправе прислать событие повторно, и
система обязана не учесть его дважды. Поэтому идемпотентность держится на event_id:
тот же идентификатор с тем же содержимым — повтор, он фиксируется и больше ни на что
не влияет; тот же идентификатор с другим содержимым — конфликт, и сообщение уходит в
карантин, а не молча перезаписывает принятое.

Обработчиков может быть несколько. Сообщения раскладываются между ними по ключу
раздела (изделие, иначе оборудование, иначе источник), поэтому события одного изделия
проверяются по порядку одним обработчиком. Запись в журнал — одна точка на всех: она
выдаёт номер записи и хеш предыдущей и потому не параллелится.
"""

from __future__ import annotations

import hashlib
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from zero_defect.config import Settings
from zero_defect.ingest.model import FieldError, SourceEvent
from zero_defect.ingest.validation import ParseResult, parse_event
from zero_defect.ledger.store import Ledger, canonical

ACCEPTED = "accepted"
DUPLICATE = "duplicate"
REJECTED = "rejected"


@dataclass(frozen=True)
class Delivery:
    """Одно пришедшее сообщение и момент, когда оно пришло."""

    raw: object
    received_at: datetime


@dataclass
class DeliveryResult:
    """Что стало с сообщением. Возвращается источнику по каждому сообщению пакета."""

    event_id: str | None
    status: str
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "status": self.status,
            "errors": self.errors,
            "warnings": self.warnings,
            "flags": self.flags,
        }


def payload_hash(raw: object) -> str:
    return hashlib.sha256(canonical({"raw": raw})).hexdigest()


def partition_of(raw: object, partitions: int) -> int:
    """Номер обработчика по ключу раздела; стабилен между запусками."""

    if not isinstance(raw, dict):
        return 0
    key = raw.get("item_id") or raw.get("equipment_id") or raw.get("source_id") or ""
    return zlib.crc32(str(key).encode()) % partitions


class IngestPipeline:
    """Проверяет пакеты сообщений и записывает итог в журнал."""

    def __init__(self, settings: Settings, ledger: Ledger) -> None:
        self.settings = settings
        self.ledger = ledger
        self._hashes: dict[str, str] = {}
        self._latest: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self.stats = {"accepted": 0, "duplicates": 0, "rejected": 0}

    def remember(self, event: SourceEvent, digest: str) -> None:
        """Учитывает событие, прочитанное из журнала при запуске."""

        self._hashes[event.event_id] = digest
        latest = self._latest.get(event.partition_key)
        if latest is None or event.occurred_at > latest:
            self._latest[event.partition_key] = event.occurred_at

    def _parse_all(self, deliveries: list[Delivery]) -> list[ParseResult]:
        workers = max(1, self.settings.workers)
        if workers == 1 or len(deliveries) < 2:
            return [parse_event(item.raw, item.received_at) for item in deliveries]
        buckets: dict[int, list[int]] = {}
        for index, item in enumerate(deliveries):
            buckets.setdefault(partition_of(item.raw, workers), []).append(index)
        results: list[ParseResult | None] = [None] * len(deliveries)

        def work(indices: list[int]) -> None:
            for index in indices:
                item = deliveries[index]
                results[index] = parse_event(item.raw, item.received_at)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, buckets.values()))
        return results  # type: ignore[return-value]

    def _refresh_known(self, event_ids: set[str]) -> None:
        """Сверяет идентификаторы с общей БД перед обработкой пакета."""

        self._hashes.update(self.ledger.event_hashes(event_ids))

    def _flags(self, event: SourceEvent, latest: datetime | None = None) -> list[str]:
        flags = []
        lag = (event.received_at - event.occurred_at).total_seconds()
        if lag > self.settings.late_after_s:
            flags.append("late")
        if -lag > self.settings.clock_skew_tolerance_s:
            flags.append("clock_skew")
        if latest is None:
            latest = self._latest.get(event.partition_key)
        if latest is not None and event.occurred_at < latest:
            flags.append("out_of_order")
        return flags

    def submit(self, deliveries: list[Delivery]) -> tuple[list[DeliveryResult], list[SourceEvent]]:
        """Обрабатывает пакет. Возвращает ответы и новые принятые события."""

        parsed = self._parse_all(deliveries)
        event_ids = {result.event.event_id for result in parsed if result.event is not None}
        with self._lock:
            # SELECT позволяет дать точный ответ на уже известный event_id. Между
            # SELECT и INSERT остаётся гонка; уникальный частичный индекс в БД
            # разрешает её атомарно, после чего мы перечитываем победившую запись.
            self._refresh_known(event_ids)
            for attempt in range(max(2, len(event_ids) + 1)):
                results: list[DeliveryResult] = []
                accepted: list[SourceEvent] = []
                entries: list[tuple[str, dict, str | None]] = []
                new_hashes: dict[str, str] = {}
                new_latest: dict[str, datetime] = {}
                counts = {"accepted": 0, "duplicates": 0, "rejected": 0}
                for delivery, outcome in zip(deliveries, parsed, strict=True):
                    raw = delivery.raw
                    event_id = raw.get("event_id") if isinstance(raw, dict) else None
                    received = delivery.received_at.isoformat()
                    warnings = [item.as_dict() for item in outcome.warnings]
                    if not outcome.ok:
                        errors = [item.as_dict() for item in outcome.errors]
                        entries.append(
                            (
                                "rejected_event",
                                {"raw": raw, "received_at": received, "errors": errors},
                                event_id,
                            )
                        )
                        results.append(DeliveryResult(event_id, REJECTED, errors, warnings))
                        counts["rejected"] += 1
                        continue
                    event = outcome.event
                    digest = payload_hash(raw)
                    known = new_hashes.get(event.event_id, self._hashes.get(event.event_id))
                    if known is not None:
                        if known == digest:
                            entries.append(
                                (
                                    "duplicate_delivery",
                                    {
                                        "event_id": event.event_id,
                                        "source_id": event.source_id,
                                        "received_at": received,
                                    },
                                    event.event_id,
                                )
                            )
                            results.append(DeliveryResult(event.event_id, DUPLICATE, [], warnings))
                            counts["duplicates"] += 1
                        else:
                            conflict = FieldError(
                                "event_id",
                                "conflicting_duplicate",
                                "идентификатор уже принят с другим содержимым; "
                                "исходное событие сохранено",
                            ).as_dict()
                            entries.append(
                                (
                                    "rejected_event",
                                    {"raw": raw, "received_at": received, "errors": [conflict]},
                                    event.event_id,
                                )
                            )
                            results.append(
                                DeliveryResult(event.event_id, REJECTED, [conflict], warnings)
                            )
                            counts["rejected"] += 1
                        continue
                    latest = new_latest.get(
                        event.partition_key, self._latest.get(event.partition_key)
                    )
                    flags = self._flags(event, latest)
                    event = replace(event, flags=tuple(flags))
                    new_hashes[event.event_id] = digest
                    if latest is None or event.occurred_at > latest:
                        new_latest[event.partition_key] = event.occurred_at
                    entries.append(
                        (
                            "source_event",
                            {
                                "event": raw,
                                "received_at": received,
                                "flags": flags,
                                "warnings": warnings,
                            },
                            event.event_id,
                        )
                    )
                    accepted.append(event)
                    results.append(DeliveryResult(event.event_id, ACCEPTED, [], warnings, flags))
                    counts["accepted"] += 1
                try:
                    headers = self.ledger.append_many(entries)
                except IntegrityError:
                    before = set(self._hashes).intersection(event_ids)
                    self._refresh_known(event_ids)
                    if set(self._hashes).intersection(event_ids) == before or attempt + 1 >= max(
                        2, len(event_ids) + 1
                    ):
                        raise
                    continue
                self._hashes.update(new_hashes)
                self._latest.update(new_latest)
                for key, value in counts.items():
                    self.stats[key] += value
                break
        by_event = {
            header.ref_id: header.seq for header in headers if header.kind == "source_event"
        }
        accepted = [replace(event, ledger_seq=by_event[event.event_id]) for event in accepted]
        return results, accepted


def now() -> datetime:
    return datetime.now(UTC)
