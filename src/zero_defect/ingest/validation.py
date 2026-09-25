"""
Проверка входного сообщения по контракту и приведение к внутренней модели.

Ответ всегда содержит полный список проблем, а не первую найденную: источник,
приславший сообщение с тремя ошибками, узнаёт о всех трёх с одного раза. Каждая
ошибка адресована путём до поля и несёт стабильный код — по коду интерфейс и
источники решают, что делать, текст сообщения служит пояснением.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from zero_defect.config import SUPPORTED_SCHEMA_VERSIONS
from zero_defect.contracts.generated import MODELS
from zero_defect.ingest.model import (
    DURATION_UNITS_S,
    Defect,
    Evidence,
    FieldError,
    ReportedDuration,
    SourceEvent,
)

# Поля, чьи значения ограничены перечислением. Нарушение в них — это не опечатка в
# формате, а значение, смысл которого системе неизвестен, поэтому у него свой код.
_ENUM_ERRORS = {"literal_error", "enum"}


@dataclass(frozen=True)
class ParseResult:
    """Итог разбора: событие либо список ошибок, и предупреждения в обоих случаях."""

    event: SourceEvent | None
    errors: tuple[FieldError, ...] = ()
    warnings: tuple[FieldError, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.event is not None


def _path(loc: tuple) -> str:
    parts: list[str] = []
    for piece in loc:
        if piece == "root":
            continue
        if isinstance(piece, int):
            parts.append(f"[{piece}]")
        else:
            parts.append(("." if parts else "") + str(piece))
    return "".join(parts) or "(сообщение)"


def _field_errors(error: ValidationError) -> list[FieldError]:
    result = []
    for item in error.errors():
        kind = item["type"]
        if kind == "missing":
            code, text = "missing_field", "обязательное поле отсутствует"
        elif kind in _ENUM_ERRORS:
            code, text = "unknown_enum_value", f"неизвестное значение: {item.get('input')!r}"
        else:
            code, text = "invalid_value", item["msg"]
        result.append(FieldError(_path(item["loc"]), code, text))
    return result


def _unknown_fields(raw: dict, known: set[str]) -> list[FieldError]:
    return [
        FieldError(
            name, "unknown_optional_field", "поле не описано в контракте и сохранено как есть"
        )
        for name in raw
        if name not in known
    ]


def parse_event(raw: object, received_at: datetime) -> ParseResult:
    """Проверяет сообщение и приводит его к SourceEvent."""

    if not isinstance(raw, dict):
        return ParseResult(None, (FieldError("(сообщение)", "not_an_object", "ожидался объект"),))
    version = raw.get("schema_version")
    if version is None:
        return ParseResult(
            None, (FieldError("schema_version", "missing_field", "не указана версия контракта"),)
        )
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(SUPPORTED_SCHEMA_VERSIONS)
        return ParseResult(
            None,
            (
                FieldError(
                    "schema_version",
                    "unknown_schema_version",
                    f"версия {version!r} не поддерживается; принимаются {supported}",
                ),
            ),
        )
    event_type = raw.get("event_type")
    model = MODELS[version].get(event_type) if isinstance(event_type, str) else None
    if model is None:
        code = "missing_field" if event_type is None else "unknown_event_type"
        return ParseResult(
            None, (FieldError("event_type", code, f"тип {event_type!r} неизвестен"),)
        )
    try:
        parsed = model.model_validate(raw)
    except ValidationError as error:
        return ParseResult(None, tuple(_field_errors(error)))
    warnings = _unknown_fields(raw, set(model.model_fields))
    data = parsed.model_dump(mode="python", exclude_none=True)
    return ParseResult(normalize(data, received_at), (), tuple(warnings))


def normalize(data: dict, received_at: datetime) -> SourceEvent:
    """Приводит проверенное сообщение любой поддерживаемой версии к SourceEvent."""

    duration = data.get("reported_duration")
    reported = None
    if duration:
        seconds = float(duration["value"]) * DURATION_UNITS_S[duration["unit"]]
        reported = ReportedDuration(
            seconds, duration["meaning"], f"{duration['value']} {duration['unit']}"
        )
    defects = tuple(
        Defect(
            defect_type=item["defect_type"],
            description=item.get("description"),
            component_item_id=item.get("component_item_id"),
            area=item.get("area"),
            severity=item.get("severity"),
            size_mm=item.get("size_mm"),
        )
        for item in data.get("defects", ())
    )
    evidence = tuple(
        Evidence(
            uri=item["uri"],
            kind=item["kind"],
            captured_at=item.get("captured_at"),
            checkpoint_id=item.get("checkpoint_id"),
        )
        for item in data.get("evidence_refs", ())
    )
    simple = {
        name: data.get(name)
        for name in (
            "sequence_no",
            "item_id",
            "item_type_id",
            "line_id",
            "station_id",
            "operation_run_id",
            "operation_id",
            "operator_id",
            "equipment_id",
            "previous_run_id",
            "rework_reason",
            "identification",
            "component_item_id",
            "work_order_id",
            "origin",
            "outcome",
            "checkpoint_id",
            "checkpoint_kind",
            "inspection_result",
            "confidence",
            "observation_quality",
            "analyzer_version",
            "action_type",
            "details",
            "machine_state",
            "message",
            "pause_reason",
            "shift_id",
        )
    }
    return SourceEvent(
        event_id=data["event_id"],
        event_type=data["event_type"],
        schema_version=data["schema_version"],
        occurred_at=data["occurred_at"],
        received_at=received_at,
        source_id=data["source_id"],
        reported_duration=reported,
        defects=defects,
        evidence=evidence,
        parameters=dict(data.get("parameters") or {}),
        **simple,
    )
