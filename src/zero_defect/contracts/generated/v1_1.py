# Сгенерировано datamodel-code-generator 0.83.0 из contracts/events/v1.1.schema.json.
# Не править руками: uv run python scripts/generate_contracts.py

from __future__ import annotations

from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel


class SchemaVersion(RootModel[Literal["1.1"]]):
    root: Literal["1.1"]
    """
    Версия контракта сообщения.
    """


class Identifier(RootModel[str]):
    root: Annotated[
        str,
        Field(max_length=128, min_length=1, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
    ]
    """
    Условный идентификатор объекта. Реальные персональные данные не передаются.
    """


class Timestamp(RootModel[AwareDatetime]):
    root: AwareDatetime
    """
    Время в ISO 8601 с указанием смещения, например 2026-09-25T10:15:00+03:00.
    """


class InspectionResult(
    RootModel[Literal["defect_signs_found", "no_defect_signs", "not_assessable"]]
):
    root: Literal["defect_signs_found", "no_defect_signs", "not_assessable"]
    """
    Признаки дефекта обнаружены, не обнаружены либо оценка невозможна.
    """


class CheckpointKind(
    RootModel[Literal["incoming", "before_operation", "after_operation", "final"]]
):
    root: Literal["incoming", "before_operation", "after_operation", "final"]
    """
    Назначение контрольной точки в маршруте изделия.
    """


class ObservationQuality(RootModel[Literal["good", "degraded", "poor", "unknown"]]):
    root: Literal["good", "degraded", "poor", "unknown"]
    """
    Качество наблюдения по оценке внешнего анализатора.
    """


class Severity(RootModel[Literal["minor", "major", "critical"]]):
    root: Literal["minor", "major", "critical"]
    """
    Тяжесть дефекта, если анализатор её сообщает.
    """


class DurationUnit(RootModel[Literal["s", "min", "h"]]):
    root: Literal["s", "min", "h"]


class DurationMeaning(
    RootModel[Literal["active_processing", "station_total", "other"]]
):
    root: Literal["active_processing", "station_total", "other"]
    """
    Что именно измеряет длительность: активную обработку, полное время на участке или иной интервал.
    """


class ReportedDuration(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    value: Annotated[float, Field(ge=0.0)]
    unit: DurationUnit
    meaning: DurationMeaning


class Defect(BaseModel):
    model_config = ConfigDict(
        extra="allow",
    )
    defect_type: Identifier
    """
    Код типа дефекта из справочника.
    """
    description: Annotated[str | None, Field(max_length=1000)] = None
    component_item_id: Identifier | None = None
    """
    Компонент, на котором найден дефект.
    """
    area: Annotated[str | None, Field(max_length=128)] = None
    """
    Зона изделия или компонента.
    """
    severity: Severity | None = None
    size_mm: Annotated[float | None, Field(ge=0.0)] = None
    """
    Размер признака дефекта по оценке анализатора, мм. Проектное предположение: требует калибровки камеры.
    """


class EvidenceRef(BaseModel):
    model_config = ConfigDict(
        extra="allow",
    )
    uri: Annotated[str, Field(max_length=1000, min_length=1)]
    kind: Literal["photo", "video"]
    captured_at: Timestamp | None = None
    checkpoint_id: Identifier | None = None


class OperationOutcome(RootModel[Literal["completed", "aborted"]]):
    root: Literal["completed", "aborted"]


class ActionType(
    RootModel[
        Literal[
            "mode_change",
            "confirmation",
            "check_skipped",
            "manual_decision",
            "tool_change",
            "other",
        ]
    ]
):
    root: Literal[
        "mode_change",
        "confirmation",
        "check_skipped",
        "manual_decision",
        "tool_change",
        "other",
    ]
    """
    Действие оператора: изменение режима, подтверждение, пропуск проверки, ручное решение.
    """


class MachineStateValue(
    RootModel[
        Literal["running", "idle", "warning", "deviation", "stopped", "maintenance"]
    ]
):
    root: Literal["running", "idle", "warning", "deviation", "stopped", "maintenance"]


class ItemRegistered(BaseModel):
    """
    Экземпляр детали или изделия поступил на учёт: из задания, при приёмке или при запуске в производство.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["item_registered"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    """
    Сквозной номер сообщения у источника; по разрывам в нём видны пропуски.
    """
    item_id: Identifier
    item_type_id: Identifier
    line_id: Identifier | None = None
    work_order_id: Identifier | None = None
    origin: Literal["purchased", "manufactured"] | None = None
    """
    Покупной компонент или изготовленный на предприятии.
    """


class ComponentLinked(BaseModel):
    """
    Компонент установлен в сборку.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["component_linked"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    item_id: Identifier
    """
    Сборка.
    """
    component_item_id: Identifier
    operation_run_id: Identifier | None = None
    station_id: Identifier | None = None


class OperationStarted(BaseModel):
    """
    Начало конкретного выполнения операции. Повторное выполнение получает новый operation_run_id и ссылается на предыдущее через previous_run_id.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["operation_started"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    item_id: Identifier
    item_type_id: Identifier | None = None
    operation_run_id: Identifier
    operation_id: Identifier
    """
    Операция техпроцесса; по ней сопоставляются сравнимые работы.
    """
    line_id: Identifier
    station_id: Identifier
    operator_id: Identifier | None = None
    equipment_id: Identifier | None = None
    previous_run_id: Identifier | None = None
    rework_reason: Annotated[str | None, Field(max_length=1000)] = None
    identification: Literal["reliable", "unreliable"] | None = None
    """
    Надёжность идентификации изделия на участке.
    """
    shift_id: Identifier | None = None
    """
    Смена по учёту источника. Если не передана, система определяет смену по расписанию сама.
    """


class OperationPaused(BaseModel):
    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["operation_paused"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    item_id: Identifier | None = None
    operation_run_id: Identifier
    pause_reason: Annotated[str | None, Field(max_length=1000)] = None


class OperationResumed(BaseModel):
    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["operation_resumed"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    item_id: Identifier | None = None
    operation_run_id: Identifier


class OperationFinished(BaseModel):
    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["operation_finished"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    item_id: Identifier
    operation_run_id: Identifier
    outcome: OperationOutcome | None = None
    reported_duration: ReportedDuration | None = None


class InspectionReported(BaseModel):
    """
    Результат уже выполненного внешнего анализа наблюдения (VisionQC или ручной контроль).
    """

    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["inspection_reported"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    item_id: Identifier
    item_type_id: Identifier | None = None
    line_id: Identifier | None = None
    station_id: Identifier | None = None
    checkpoint_id: Identifier
    checkpoint_kind: CheckpointKind
    operation_run_id: Identifier | None = None
    """
    Операция, после которой выполнен контроль.
    """
    inspection_result: InspectionResult
    defects: list[Defect] | None = None
    confidence: Annotated[float | None, Field(ge=0.0, le=1.0)] = None
    observation_quality: ObservationQuality | None = None
    analyzer_version: Annotated[str | None, Field(max_length=64)] = None
    evidence_refs: list[EvidenceRef] | None = None
    shift_id: Identifier | None = None
    """
    Смена по учёту источника. Если не передана, система определяет смену по расписанию сама.
    """


class OperatorAction(BaseModel):
    """
    Действие оператора, сообщённое OperatorVision или терминалом участка.
    """

    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["operator_action"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    operator_id: Identifier
    action_type: ActionType
    item_id: Identifier | None = None
    operation_run_id: Identifier | None = None
    station_id: Identifier | None = None
    equipment_id: Identifier | None = None
    details: Annotated[str | None, Field(max_length=1000)] = None
    confidence: Annotated[float | None, Field(ge=0.0, le=1.0)] = None
    shift_id: Identifier | None = None
    """
    Смена по учёту источника. Если не передана, система определяет смену по расписанию сама.
    """


class MachineState(BaseModel):
    """
    Состояние, параметры, предупреждение или остановка оборудования (MachineLogs).
    """

    model_config = ConfigDict(
        extra="allow",
    )
    event_id: Identifier
    event_type: Literal["machine_state"]
    schema_version: SchemaVersion
    occurred_at: Timestamp
    source_id: Identifier
    sequence_no: Annotated[int | None, Field(ge=0)] = None
    equipment_id: Identifier
    station_id: Identifier | None = None
    line_id: Identifier | None = None
    machine_state: MachineStateValue
    parameters: dict[str, float] | None = None
    message: Annotated[str | None, Field(max_length=1000)] = None


class ProductionEvent(
    RootModel[
        ItemRegistered
        | ComponentLinked
        | OperationStarted
        | OperationPaused
        | OperationResumed
        | OperationFinished
        | InspectionReported
        | OperatorAction
        | MachineState
    ]
):
    root: Annotated[
        ItemRegistered
        | ComponentLinked
        | OperationStarted
        | OperationPaused
        | OperationResumed
        | OperationFinished
        | InspectionReported
        | OperatorAction
        | MachineState,
        Field(title="ProductionEvent"),
    ]
    """
    Событие производственной линии, контракт zd-events версии 1.1. Версия контракта не связана с версией анализирующего модуля (analyzer_version) и с версией приложения. Отличия от 1.0: необязательные shift_id и defects[].size_mm, новое значение tool_change в ActionType.
    """
