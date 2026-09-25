"""
Ядро системы: сквозной процесс от события до решения по изделию.

Здесь сходятся стадии конвейера — приём, журнал, история, несоответствия, разбор и
аналитика. Ядро не знает ни про HTTP, ни про внешние системы: интерфейс и адаптеры
вызывают его методы, а о решениях узнают через подписку. Поэтому смена транспорта или
внешней системы не затрагивает правил разбора.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from zero_defect.analysis.causes import assess
from zero_defect.analysis.metrics import compute
from zero_defect.config import Settings, resolve_storage_url
from zero_defect.history.projection import History, build_history
from zero_defect.ingest.pipeline import Delivery, DeliveryResult, IngestPipeline, payload_hash
from zero_defect.ingest.validation import parse_event
from zero_defect.ledger.crypto import PROFILES, Keyring
from zero_defect.ledger.store import IntegrityReport, Ledger
from zero_defect.observability import InMemoryTelemetry, Telemetry
from zero_defect.quality.nonconformance import (
    CAUSE_ACTION,
    Decision,
    Nonconformance,
    build_nonconformances,
    check_decision,
    item_status,
)
from zero_defect.security.auth import Principal
from zero_defect.security.bus import SecurityBus
from zero_defect.security.users import UserStore
from zero_defect.storage.database import close_database, open_database

# Кто какое решение вправе принять. Подтвердить или отклонить несоответствие —
# контролёр; установить причину — технолог или контролёр.
DECISION_PERMISSION = {CAUSE_ACTION: "set_cause"}

Listener = Callable[[str, dict], None]


class DecisionError(Exception):
    """Решение недопустимо: нет карточки, неверный статус или нет обоснования."""


@dataclass
class State:
    """Снимок всех производных представлений. Строится из журнала целиком."""

    history: History
    cards: dict[str, Nonconformance]
    statuses: dict[str, str]
    metrics: dict
    quarantine: list[dict] = field(default_factory=list)
    unreadable: list[dict] = field(default_factory=list)
    duplicates: int = 0


class QualitySystem:
    """Единая точка входа в ядро для API, скриптов, сценариев и тестов."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.keyring = Keyring(settings.keys_dir)
        self.keyring.ensure(settings.crypto_profile)
        # Смена профиля в конфигурации выпускает новый ключ нового профиля. Прежние
        # записи остаются под своим профилем и проверяются им же.
        if self.keyring.active().profile_id != settings.crypto_profile:
            self.keyring.rotate(settings.crypto_profile)
        self.database = open_database(resolve_storage_url(settings))
        self.ledger = Ledger(self.database, self.keyring)
        self.pipeline = IngestPipeline(settings, self.ledger)
        self.users = UserStore(self.database, settings.users_path)
        self.security = SecurityBus(self.keyring, self.users, self._audit_sink)
        self.listeners: list[Listener] = []
        self._events = []
        # Исходные сообщения как пришли: карточка несоответствия показывает их без правок.
        self._raw: dict[str, dict] = {}
        self.telemetry: Telemetry = InMemoryTelemetry()
        self._decisions: list[Decision] = []
        self._quarantine: list[dict] = []
        self._unreadable: list[dict] = []
        self._critical: list[dict] = []
        self._integration: list[dict] = []
        self._duplicates = 0
        self._state: State | None = None
        # Версия данных растёт с каждым принятым событием или решением. Снимок помнит, из
        # какой версии построен, и пересобирается, только когда она устарела.
        self._version = 0
        self._state_version = -1
        self._state_built = 0.0
        self._build_lock = threading.Lock()
        self._moments: dict[str, State] = {}
        self._moments_version = -1
        self._lock = threading.RLock()
        self._load()

    # --- загрузка ----------------------------------------------------------------

    def _load(self) -> None:
        for record in self.ledger.records():
            header = record.header
            if record.payload is None:
                self._unreadable.append({**header.as_dict(), "status": record.status})
                continue
            payload = record.payload
            if header.kind == "source_event":
                received = datetime.fromisoformat(payload["received_at"])
                parsed = parse_event(payload["event"], received)
                if parsed.event is None:
                    self._unreadable.append({**header.as_dict(), "status": "no_longer_valid"})
                    continue
                event = replace(
                    parsed.event, flags=tuple(payload.get("flags", ())), ledger_seq=header.seq
                )
                self.pipeline.remember(event, payload_hash(payload["event"]))
                self._events.append(event)
                self._raw[event.event_id] = payload["event"]
            elif header.kind == "rejected_event":
                self._quarantine.append({"seq": header.seq, **payload})
            elif header.kind == "duplicate_delivery":
                self._duplicates += 1
            elif header.kind == "decision":
                self._decisions.append(_decision_from(payload, header.seq))
            elif header.kind == "critical_action":
                self._critical.append({"seq": header.seq, **payload})
            elif header.kind == "integration_exchange":
                self._integration.append({"seq": header.seq, **payload})

    # --- приём -------------------------------------------------------------------

    def ingest(self, raws: list, received_at: datetime | None = None) -> list[DeliveryResult]:
        moment = received_at or datetime.now(UTC)
        return self.ingest_deliveries([Delivery(raw, moment) for raw in raws])

    def ingest_deliveries(self, deliveries: list[Delivery]) -> list[DeliveryResult]:
        started = time.perf_counter()
        results, accepted = self.pipeline.submit(deliveries)
        self.telemetry.observe("ingest.batch_seconds", time.perf_counter() - started)
        for result in results:
            self.telemetry.count("ingest.messages", status=result.status)
        raw_by_id = {
            delivery.raw.get("event_id"): delivery.raw
            for delivery in deliveries
            if isinstance(delivery.raw, dict)
        }
        with self._lock:
            self._events.extend(accepted)
            for event in accepted:
                self._raw[event.event_id] = raw_by_id.get(event.event_id, {})
            for result, delivery in zip(results, deliveries, strict=True):
                if result.status == "rejected":
                    self._quarantine.append(
                        {
                            "raw": delivery.raw,
                            "received_at": delivery.received_at.isoformat(),
                            "errors": result.errors,
                        }
                    )
                elif result.status == "duplicate":
                    self._duplicates += 1
            if accepted:
                self._version += 1
        if accepted:
            self._notify("events", {"count": len(accepted)})
        return results

    # --- решения -----------------------------------------------------------------

    def decide(
        self,
        principal: Principal,
        nc_id: str,
        action: str,
        reason: str,
        cause_category: str | None = None,
        decided_at: datetime | None = None,
    ) -> Decision:
        """Записывает решение человека отдельной записью; исходные сообщения не меняются."""

        permission = DECISION_PERMISSION.get(action, "decide")
        if action == CAUSE_ACTION and principal.role == "controller":
            permission = "decide"
        self.security.authorize(principal, permission, "decide", {"nc_id": nc_id, "action": action})
        if not reason or not reason.strip():
            raise DecisionError("решение без обоснования не принимается")
        with self._lock:
            card = self.state().cards.get(nc_id)
            if card is None:
                raise DecisionError(f"несоответствие {nc_id} не найдено")
            problem = check_decision(card, action, cause_category)
            if problem:
                raise DecisionError(problem)
            decision = Decision(
                decision_id=str(uuid.uuid4()),
                nc_id=nc_id,
                action=action,
                author_id=principal.user_id,
                author_role=principal.role,
                reason=reason.strip(),
                decided_at=decided_at or datetime.now(UTC),
                cause_category=cause_category,
            )
            header = self.ledger.append("decision", decision.as_dict(), ref_id=nc_id)
            decision = replace(decision, ledger_seq=header.seq)
            self._decisions.append(decision)
            self._version += 1
        self._notify("decision", {"decision": decision, "item_id": card.item_id})
        return decision

    # --- аудит и защита ------------------------------------------------------------

    def _audit_sink(self, action: str, principal: Principal, details: dict) -> None:
        payload = {
            "action": action,
            "user_id": principal.user_id,
            "role": principal.role,
            "details": details,
            "at": datetime.now(UTC).isoformat(),
        }
        header = self.ledger.append("critical_action", payload, ref_id=principal.user_id)
        self._critical.append({"seq": header.seq, **payload})

    def audit(self, action: str, principal: Principal, details: dict | None = None) -> None:
        self._audit_sink(action, principal, details or {})

    def record_exchange(self, payload: dict) -> None:
        header = self.ledger.append(
            "integration_exchange", payload, ref_id=payload.get("message_id")
        )
        self._integration.append({"seq": header.seq, **payload})

    def verify_integrity(self) -> IntegrityReport:
        return self.ledger.verify()

    def rotate_key(self, profile_id: str | None = None) -> dict:
        if profile_id is not None and profile_id not in PROFILES:
            raise ValueError(f"профиль {profile_id} неизвестен")
        info = self.keyring.rotate(profile_id)
        return {
            "key_id": info.key_id,
            "key_version": info.key_version,
            "profile_id": info.profile_id,
        }

    @property
    def critical_actions(self) -> list[dict]:
        return list(self._critical)

    @property
    def exchanges(self) -> list[dict]:
        return list(self._integration)

    # --- представления -------------------------------------------------------------

    def state(self, max_age: float | None = None) -> State:
        """Снимок всех представлений.

        Без max_age — всегда по последним данным: так работают решения людей. С max_age
        допускается снимок не старше max_age секунд: так читают опросы интерфейса. Пока
        идёт эмуляция, события приходят каждую секунду, и пересборка на каждый опрос
        держала бы сервис занятым только ею.
        """

        with self._build_lock:
            with self._lock:
                version = self._version
                fresh = self._state is not None and self._state_version == version
                young = (
                    self._state is not None
                    and max_age is not None
                    and time.monotonic() - self._state_built < max_age
                )
                if fresh or young:
                    return self._state
                events, decisions = list(self._events), list(self._decisions)
            # Пересборка идёт вне общей блокировки: приём событий в это время не ждёт.
            state = self._build(events, decisions)
            with self._lock:
                self._state, self._state_version = state, version
                self._state_built = time.monotonic()
            return state

    def snapshot(self) -> State:
        """Снимок для чтения интерфейсом: не старше секунды."""

        return self.state(max_age=1.0)

    def state_at(self, moment: datetime) -> State:
        """Состояние системы на момент времени: события и решения, случившиеся до него.

        Шкала времени интерфейса и переход к моменту отказа из очереди решений смотрят
        сюда. Будущее относительно момента не видно: ни поздно пришедших событий, ни
        решений, принятых позже.
        """

        key = moment.isoformat(timespec="seconds")
        with self._lock:
            if self._moments_version != self._version:
                self._moments, self._moments_version = {}, self._version
            cached = self._moments.get(key)
            if cached is not None:
                return cached
            events = [event for event in self._events if event.occurred_at <= moment]
            decisions = [item for item in self._decisions if item.decided_at <= moment]
        state = self._build(events, decisions)
        with self._lock:
            # Шкалу времени двигают рывками, поэтому помнится дюжина последних моментов.
            if len(self._moments) >= 12:
                self._moments.pop(next(iter(self._moments)))
            self._moments[key] = state
        return state

    def _build(self, events: list, decisions: list[Decision]) -> State:
        history = build_history(events, self.settings)
        cards = build_nonconformances(history, decisions)
        for card in cards.values():
            card.assessment = assess(card, history, self.settings).as_dict()
        statuses = {item_id: item_status(item_id, history, cards) for item_id in history.items}
        return State(
            history=history,
            cards=cards,
            statuses=statuses,
            metrics=compute(history, cards, statuses),
            quarantine=list(self._quarantine),
            unreadable=list(self._unreadable),
            duplicates=self._duplicates,
        )

    def raw_event(self, event_id: str) -> dict | None:
        return self._raw.get(event_id)

    def ingest_summary(self) -> dict:
        events = self._events
        return {
            "accepted": len(events),
            "duplicates": self._duplicates,
            "rejected": len(self._quarantine),
            "late": sum(1 for event in events if "late" in event.flags),
            "out_of_order": sum(1 for event in events if "out_of_order" in event.flags),
            "clock_skew": sum(1 for event in events if "clock_skew" in event.flags),
            "unreadable_records": len(self._unreadable),
        }

    def _notify(self, kind: str, payload: dict) -> None:
        for listener in list(self.listeners):
            listener(kind, payload)

    def close(self) -> None:
        close_database(self.database.url)


def _decision_from(payload: dict, seq: int) -> Decision:
    return Decision(
        decision_id=payload["decision_id"],
        nc_id=payload["nc_id"],
        action=payload["action"],
        author_id=payload["author_id"],
        author_role=payload["author_role"],
        reason=payload["reason"],
        decided_at=datetime.fromisoformat(payload["decided_at"]),
        cause_category=payload.get("cause_category"),
        ledger_seq=seq,
    )
