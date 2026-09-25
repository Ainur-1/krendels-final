"""
История изделия, собранная из принятых событий.

Это изменяемое представление, а не хранилище: оно целиком строится из журнала и
пересобирается заново при любом новом событии. Поэтому позднее событие не требует
особой логики «вставки задним числом» — события сортируются по времени возникновения,
и история получается той же, в каком бы порядке они ни пришли.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from zero_defect.config import Settings
from zero_defect.ingest.model import SourceEvent

# Состояния станка, которые считаются отклонением и попадают в разбор обстоятельств.
MACHINE_DEVIATIONS = ("warning", "deviation", "stopped")

# Качество наблюдения, при котором «признаков не обнаружено» нельзя считать годностью.
UNRELIABLE_QUALITY = ("poor", "unknown")


@dataclass
class Run:
    """Одно конкретное выполнение операции над изделием."""

    run_id: str
    item_id: str | None = None
    operation_id: str | None = None
    line_id: str | None = None
    station_id: str | None = None
    operator_id: str | None = None
    equipment_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    outcome: str | None = None
    reported_duration_s: float | None = None
    reported_duration_meaning: str | None = None
    previous_run_id: str | None = None
    rework_reason: str | None = None
    identification: str | None = None
    shift_id: str | None = None
    shift_origin: str | None = None
    pauses: list[list[datetime | None]] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    machine_event_ids: list[str] = field(default_factory=list)
    action_event_ids: list[str] = field(default_factory=list)

    @property
    def is_rework(self) -> bool:
        return self.previous_run_id is not None

    @property
    def status(self) -> str:
        if self.started_at is None:
            return "finished_without_start" if self.finished_at else "unknown"
        if self.finished_at is None:
            return "in_progress"
        return self.outcome or "completed"

    @property
    def station_time_s(self) -> float | None:
        """Полное время на участке по отметкам начала и завершения. Определено системой."""

        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def paused_s(self) -> float:
        total = 0.0
        for start, end in self.pauses:
            if start is not None and end is not None:
                total += (end - start).total_seconds()
        return total

    @property
    def active_time_s(self) -> float | None:
        """Время обработки без пауз. Определено системой по событиям пауз."""

        station = self.station_time_s
        return None if station is None else max(station - self.paused_s, 0.0)


@dataclass
class Observation:
    """Результат контроля с оценкой того, насколько ему можно доверять."""

    event: SourceEvent
    effective_result: str
    reliable: bool
    reliability_note: str | None = None

    @property
    def occurred_at(self) -> datetime:
        return self.event.occurred_at


@dataclass
class Item:
    """Экземпляр детали или изделия."""

    item_id: str
    item_type_id: str | None = None
    line_id: str | None = None
    work_order_id: str | None = None
    origin: str | None = None
    registered_at: datetime | None = None
    registered: bool = False
    parent_id: str | None = None
    components: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)


@dataclass
class History:
    """Всё, что известно о линии, в разрезе изделий, операций и оборудования."""

    events: list[SourceEvent]
    items: dict[str, Item]
    runs: dict[str, Run]
    observations: list[Observation]
    machine_events: dict[str, list[SourceEvent]]
    actions: list[SourceEvent]
    events_by_id: dict[str, SourceEvent]
    source_sequences: dict[str, list[int]]

    def ancestors(self, item_id: str) -> list[str]:
        chain = []
        current = self.items.get(item_id)
        while current is not None and current.parent_id and current.parent_id not in chain:
            chain.append(current.parent_id)
            current = self.items.get(current.parent_id)
        return chain

    def source_gaps(self) -> dict[str, list[list[int]]]:
        """Пропуски в сквозной нумерации источников: диапазоны недошедших номеров."""

        gaps: dict[str, list[list[int]]] = {}
        for source, numbers in self.source_sequences.items():
            ordered = sorted(set(numbers))
            ranges = []
            for low, high in zip(ordered, ordered[1:], strict=False):
                if high - low > 1:
                    ranges.append([low + 1, high - 1])
            if ranges:
                gaps[source] = ranges
        return gaps


def shift_for(moment: datetime, settings: Settings) -> str | None:
    local = moment.astimezone(settings.timezone).time()
    for shift in settings.shifts:
        if shift.contains(local):
            return shift.id
    return None


def _judge(event: SourceEvent, settings: Settings) -> Observation:
    result = event.inspection_result or "not_assessable"
    quality = event.observation_quality or "unknown"
    low_confidence = event.confidence is not None and event.confidence < settings.min_confidence
    notes = []
    if quality in UNRELIABLE_QUALITY:
        notes.append(f"качество наблюдения: {quality}")
    if low_confidence:
        notes.append(
            f"уверенность {event.confidence:.2f} ниже порога {settings.min_confidence:.2f}"
        )
    reliable = result != "not_assessable" and not notes
    effective = result
    # Отсутствие признаков при плохом наблюдении — не подтверждение годности: такой
    # результат переводится в «оценка невозможна» и не закрывает вопрос о дефекте.
    if result == "no_defect_signs" and notes:
        effective = "not_assessable"
    note = "; ".join(notes) if notes else None
    if result == "not_assessable" and note is None:
        note = "анализатор сообщил, что оценка невозможна"
    return Observation(event, effective, reliable, note)


def build_history(events: list[SourceEvent], settings: Settings) -> History:
    """Собирает историю из событий в любом порядке поступления."""

    ordered = sorted(events, key=lambda event: (event.occurred_at, event.ledger_seq))
    items: dict[str, Item] = {}
    runs: dict[str, Run] = {}
    observations: list[Observation] = []
    machine_events: dict[str, list[SourceEvent]] = defaultdict(list)
    actions: list[SourceEvent] = []
    sequences: dict[str, list[int]] = defaultdict(list)

    def item(item_id: str) -> Item:
        if item_id not in items:
            items[item_id] = Item(item_id)
        return items[item_id]

    def run(run_id: str) -> Run:
        if run_id not in runs:
            runs[run_id] = Run(run_id)
        return runs[run_id]

    for event in ordered:
        if event.sequence_no is not None:
            sequences[event.source_id].append(event.sequence_no)
        if event.item_id:
            target = item(event.item_id)
            target.event_ids.append(event.event_id)
            if event.item_type_id and not target.item_type_id:
                target.item_type_id = event.item_type_id
        kind = event.event_type
        if kind == "item_registered":
            target = item(event.item_id)
            target.registered = True
            target.registered_at = event.occurred_at
            target.line_id = event.line_id or target.line_id
            target.work_order_id = event.work_order_id
            target.origin = event.origin
        elif kind == "component_linked":
            parent, child = item(event.item_id), item(event.component_item_id)
            if child.item_id not in parent.components:
                parent.components.append(child.item_id)
            child.parent_id = parent.item_id
            child.event_ids.append(event.event_id)
        elif kind == "operation_started":
            current = run(event.operation_run_id)
            current.item_id = event.item_id
            current.operation_id = event.operation_id
            current.line_id = event.line_id
            current.station_id = event.station_id
            current.operator_id = event.operator_id
            current.equipment_id = event.equipment_id
            current.started_at = event.occurred_at
            current.previous_run_id = event.previous_run_id
            current.rework_reason = event.rework_reason
            current.identification = event.identification
            if event.shift_id:
                current.shift_id, current.shift_origin = event.shift_id, "reported"
            else:
                current.shift_id, current.shift_origin = (
                    shift_for(event.occurred_at, settings),
                    "system",
                )
            current.event_ids.append(event.event_id)
            if current.run_id not in item(event.item_id).run_ids:
                item(event.item_id).run_ids.append(current.run_id)
        elif kind == "operation_paused":
            current = run(event.operation_run_id)
            current.pauses.append([event.occurred_at, None])
            current.event_ids.append(event.event_id)
        elif kind == "operation_resumed":
            current = run(event.operation_run_id)
            open_pause = next((pause for pause in current.pauses if pause[1] is None), None)
            if open_pause is not None:
                open_pause[1] = event.occurred_at
            current.event_ids.append(event.event_id)
        elif kind == "operation_finished":
            current = run(event.operation_run_id)
            current.item_id = current.item_id or event.item_id
            current.finished_at = event.occurred_at
            current.outcome = event.outcome or "completed"
            if event.reported_duration is not None:
                current.reported_duration_s = event.reported_duration.seconds
                current.reported_duration_meaning = event.reported_duration.meaning
            current.event_ids.append(event.event_id)
            if event.item_id and current.run_id not in item(event.item_id).run_ids:
                item(event.item_id).run_ids.append(current.run_id)
        elif kind == "inspection_reported":
            observation = _judge(event, settings)
            observations.append(observation)
            item(event.item_id).observations.append(observation)
        elif kind == "operator_action":
            actions.append(event)
            if event.operation_run_id:
                run(event.operation_run_id).action_event_ids.append(event.event_id)
        elif kind == "machine_state":
            machine_events[event.equipment_id].append(event)

    # Состояния станка связываются с операцией по оборудованию и окну времени. События
    # станка уже отсортированы по времени, поэтому окно находится двоичным поиском. Прежний
    # перебор «каждое выполнение × каждое событие станка» был квадратичным: на 1704
    # изделиях он давал 2,3 млн сравнений и 73 % времени пересборки истории.
    window = timedelta(seconds=settings.machine_window_s)
    last_moment = ordered[-1].occurred_at if ordered else None
    moments = {
        equipment: [event.occurred_at for event in events]
        for equipment, events in machine_events.items()
    }
    for current in runs.values():
        if current.equipment_id is None or current.started_at is None:
            continue
        events = machine_events.get(current.equipment_id)
        if not events:
            continue
        end = current.finished_at or last_moment
        low = bisect_left(moments[current.equipment_id], current.started_at - window)
        high = bisect_right(moments[current.equipment_id], end + window)
        current.machine_event_ids.extend(event.event_id for event in events[low:high])

    return History(
        events=ordered,
        items=items,
        runs=runs,
        observations=observations,
        machine_events=dict(machine_events),
        actions=actions,
        events_by_id={event.event_id: event for event in ordered},
        source_sequences=dict(sequences),
    )
