"""
Внутренняя модель события — то, с чем работает ядро.

Ядро не видит ни JSON, ни версии контракта: сообщения версий 1.0 и 1.1 приводятся к
одному виду на границе приёма. Поэтому новая версия контракта требует правки только
нормализации, а не правил разбора несоответствий.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Единицы длительности, в которых источник вправе её передать, в секундах.
DURATION_UNITS_S = {"s": 1.0, "min": 60.0, "h": 3600.0}


@dataclass(frozen=True)
class Defect:
    """Один признак дефекта в сообщении анализатора."""

    defect_type: str
    description: str | None = None
    component_item_id: str | None = None
    area: str | None = None
    severity: str | None = None
    size_mm: float | None = None


@dataclass(frozen=True)
class Evidence:
    """Ссылка на фото или видео. Сам материал в системе может и отсутствовать."""

    uri: str
    kind: str
    captured_at: datetime | None = None
    checkpoint_id: str | None = None


@dataclass(frozen=True)
class ReportedDuration:
    """Длительность, переданная источником, с пояснением, что она измеряет."""

    seconds: float
    meaning: str
    original: str


@dataclass(frozen=True)
class SourceEvent:
    """Принятое событие производственной линии в нормализованном виде."""

    event_id: str
    event_type: str
    schema_version: str
    occurred_at: datetime
    received_at: datetime
    source_id: str
    sequence_no: int | None = None
    item_id: str | None = None
    item_type_id: str | None = None
    line_id: str | None = None
    station_id: str | None = None
    operation_run_id: str | None = None
    operation_id: str | None = None
    operator_id: str | None = None
    equipment_id: str | None = None
    previous_run_id: str | None = None
    rework_reason: str | None = None
    identification: str | None = None
    component_item_id: str | None = None
    work_order_id: str | None = None
    origin: str | None = None
    outcome: str | None = None
    reported_duration: ReportedDuration | None = None
    checkpoint_id: str | None = None
    checkpoint_kind: str | None = None
    inspection_result: str | None = None
    defects: tuple[Defect, ...] = ()
    confidence: float | None = None
    observation_quality: str | None = None
    analyzer_version: str | None = None
    evidence: tuple[Evidence, ...] = ()
    action_type: str | None = None
    details: str | None = None
    machine_state: str | None = None
    parameters: dict[str, float] = field(default_factory=dict)
    message: str | None = None
    pause_reason: str | None = None
    shift_id: str | None = None
    flags: tuple[str, ...] = ()
    ledger_seq: int = 0

    @property
    def partition_key(self) -> str:
        """По этому ключу события раскладываются между обработчиками.

        События одного изделия всегда попадают к одному обработчику, поэтому их порядок
        сохраняется при любом числе обработчиков. У событий без изделия ключом служит
        оборудование, а в крайнем случае — источник.
        """

        return self.item_id or self.equipment_id or self.source_id


@dataclass(frozen=True)
class FieldError:
    """Одна проблема входного сообщения, адресованная путём до поля."""

    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}
