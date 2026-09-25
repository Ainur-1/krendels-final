"""Общие помощники тестов: минимальное корректное событие и опорный момент времени."""

from __future__ import annotations

from datetime import UTC, datetime


def message(event_id: str = "EV-1", **fields) -> dict:
    """Минимальное корректное событие; поля переопределяются аргументами."""

    base = {
        "event_id": event_id,
        "event_type": "item_registered",
        "schema_version": "1.0",
        "occurred_at": "2026-09-25T10:00:00+03:00",
        "source_id": "mes-gw",
        "item_id": "B-1",
        "item_type_id": "BODY-K1",
    }
    base.update(fields)
    return base


NOW = datetime(2026, 9, 25, 7, 1, tzinfo=UTC)
