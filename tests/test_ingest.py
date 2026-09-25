"""
Приём: повторы, конфликты, опоздания, пропуски и сохранение исходного сообщения.

Каждое правило приёма — это то, от чего зависит честность показателей: повтор,
учтённый дважды, завышает дефекты, а опоздавшее событие, вставленное по времени
прихода, искажает историю изделия.
"""

from __future__ import annotations

from datetime import timedelta

from tests.helpers import NOW, message


def test_duplicate_is_recorded_once(system):
    first = system.ingest([message()], received_at=NOW)
    again = system.ingest([message()], received_at=NOW + timedelta(seconds=30))
    assert [r.status for r in first + again] == ["accepted", "duplicate"]
    assert system.ingest_summary()["accepted"] == 1
    assert system.ingest_summary()["duplicates"] == 1


def test_same_id_different_content_goes_to_quarantine(system):
    system.ingest([message()], received_at=NOW)
    result = system.ingest([message(item_type_id="BODY-K9")], received_at=NOW)[0]
    assert result.status == "rejected"
    assert result.errors[0]["code"] == "conflicting_duplicate"
    items = system.state().history.items
    assert items["B-1"].item_type_id == "BODY-K1"


def test_duplicate_inside_one_batch(system):
    results = system.ingest([message(), message()], received_at=NOW)
    assert [r.status for r in results] == ["accepted", "duplicate"]


def test_late_and_out_of_order_flags(system):
    system.ingest([message("E2", occurred_at="2026-09-25T10:30:00+03:00")], received_at=NOW)
    late = system.ingest(
        [message("E1", occurred_at="2026-09-25T09:00:00+03:00")],
        received_at=NOW + timedelta(hours=1),
    )[0]
    assert set(late.flags) == {"late", "out_of_order"}


def test_future_timestamp_is_clock_skew(system):
    result = system.ingest([message(occurred_at="2026-09-25T12:00:00+03:00")], received_at=NOW)[0]
    assert "clock_skew" in result.flags


def test_history_is_ordered_by_occurrence_not_arrival(system):
    base = dict(event_type="machine_state", equipment_id="CNC-01", item_id=None)
    later = message("E2", machine_state="stopped", occurred_at="2026-09-25T10:10:00+03:00", **base)
    earlier = message(
        "E1", machine_state="running", occurred_at="2026-09-25T10:05:00+03:00", **base
    )
    for raw in (later, earlier):
        raw.pop("item_id")
        raw.pop("item_type_id")
    system.ingest([later], received_at=NOW)
    system.ingest([earlier], received_at=NOW + timedelta(minutes=20))
    states = [event.machine_state for event in system.state().history.machine_events["CNC-01"]]
    assert states == ["running", "stopped"]


def test_sequence_gap_is_visible(system):
    batch = [message(f"E{n}", item_id=f"B-{n}", sequence_no=n) for n in (1, 2, 5)]
    system.ingest(batch, received_at=NOW)
    assert system.state().history.source_gaps() == {"mes-gw": [[3, 4]]}


def test_rejected_message_is_kept_with_reasons(system):
    raw = message()
    del raw["item_id"]
    system.ingest([raw], received_at=NOW)
    quarantine = system.state().quarantine
    assert quarantine[0]["raw"] == raw
    assert quarantine[0]["errors"][0]["code"] == "missing_field"


def test_state_survives_restart(settings, system):
    from zero_defect.service import QualitySystem

    system.ingest([message(), message("E2", item_id="B-2")], received_at=NOW)
    system.ingest([message()], received_at=NOW)
    system.close()
    reopened = QualitySystem(settings)
    try:
        assert reopened.ingest_summary()["accepted"] == 2
        assert reopened.ingest([message()], received_at=NOW)[0].status == "duplicate"
    finally:
        reopened.close()
