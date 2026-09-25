"""
Внутренняя модель обмена с внешними системами и порт, который реализует каждый адаптер.

Внутренняя модель и форматы внешних систем разведены: адаптер переводит одно в другое
на границе, а ядро видит только эти классы. Поэтому изменение формата 1С или MES
меняет один адаптер и не трогает производственных правил.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PlannedItem:
    """Экземпляр, который задание предписывает изготовить или принять."""

    item_id: str
    item_type_id: str
    origin: str
    parent_id: str | None = None


@dataclass(frozen=True)
class WorkOrder:
    """Производственное задание во внутреннем представлении."""

    work_order_id: str
    item_type_id: str
    line_id: str | None
    items: tuple[PlannedItem, ...]
    due_date: str | None = None
    external_id: str | None = None


@dataclass(frozen=True)
class ReferenceData:
    """Справочники: типы изделий, коды дефектов, участки."""

    item_types: dict[str, str] = field(default_factory=dict)
    defect_types: dict[str, str] = field(default_factory=dict)
    stations: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityResult:
    """Итог контроля изделия, который уходит во внешнюю систему.

    message_id вычисляется из содержимого, поэтому повторная отправка того же итога —
    это тот же идентификатор, и принимающая сторона может отбросить повтор.
    """

    message_id: str
    work_order_id: str | None
    item_id: str
    item_type_id: str | None
    verdict: str
    nonconformances: tuple[dict, ...] = ()
    decided_at: str | None = None


@dataclass(frozen=True)
class Ack:
    """Подтверждение приёма или ошибка от внешней системы."""

    message_id: str
    accepted: bool
    external_ref: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False


class ExternalSystem(Protocol):
    """Порт внешней системы. Любой адаптер реализует эти три операции."""

    name: str

    def fetch_work_orders(self) -> list[WorkOrder]: ...

    def fetch_reference(self) -> ReferenceData: ...

    def send_result(self, result: QualityResult) -> Ack: ...


class IntegrationError(Exception):
    """Внешняя система недоступна или ответила не по контракту."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable
