"""
Несоответствия и решения: статусы, права ролей, обоснование и отделение от исходного.
"""

from __future__ import annotations

import pytest

from tests.helpers import NOW, message
from zero_defect.security.auth import AuthError
from zero_defect.service import DecisionError


def inspection(event_id: str, result: str, **fields) -> dict:
    return message(
        event_id,
        event_type="inspection_reported",
        source_id="vision-mill",
        checkpoint_id="CP-MILL-01",
        checkpoint_kind="after_operation",
        inspection_result=result,
        observation_quality=fields.pop("quality", "good"),
        confidence=fields.pop("confidence", 0.9),
        **fields,
    )


@pytest.fixture()
def card(system):
    burr = [{"defect_type": "BURR", "area": "кромка"}]
    system.ingest(
        [message(), inspection("I1", "defect_signs_found", defects=burr)], received_at=NOW
    )
    return next(iter(system.state().cards.values()))


def test_signal_creates_reported_card_without_blame(card):
    assert card.status == "reported"
    assert card.assessment["operator_blamed"] is False


def test_controller_decides_and_raw_signal_stays(system, users, card):
    system.decide(users["controller"], card.nc_id, "confirm", "подтверждено осмотром")
    updated = system.state().cards[card.nc_id]
    assert updated.status == "confirmed"
    assert updated.signals[0].observation.event.inspection_result == "defect_signs_found"
    assert system.raw_event("I1")["inspection_result"] == "defect_signs_found"
    assert updated.decisions[0].author_id == "controller"


def test_reason_is_mandatory(system, users, card):
    with pytest.raises(DecisionError):
        system.decide(users["controller"], card.nc_id, "confirm", "   ")


# Подтверждают контролёр ОТК и мастер участка; остальные роли — нет.
@pytest.mark.parametrize("user", ["manager", "technologist", "administrator"])
def test_only_controller_and_master_confirm(system, users, card, user):
    with pytest.raises(AuthError) as error:
        system.decide(users[user], card.nc_id, "confirm", "попытка")
    assert error.value.status == 403
    assert system.critical_actions[-1]["action"] == "denied"


def test_technologist_sets_cause_only_after_confirmation(system, users, card):
    with pytest.raises(DecisionError):
        system.decide(users["technologist"], card.nc_id, "confirm_cause", "рано", "process_issue")
    system.decide(users["controller"], card.nc_id, "confirm", "подтверждено")
    system.decide(
        users["technologist"], card.nc_id, "confirm_cause", "режим резания", "process_issue"
    )
    assert system.state().cards[card.nc_id].confirmed_cause == "process_issue"


def test_invalid_transition_is_refused(system, users, card):
    system.decide(users["controller"], card.nc_id, "reject", "ложное срабатывание")
    with pytest.raises(DecisionError):
        system.decide(users["controller"], card.nc_id, "close", "нечего закрывать")


def test_decisions_survive_restart(settings, system, users, card):
    from zero_defect.service import QualitySystem

    system.decide(users["controller"], card.nc_id, "confirm", "подтверждено")
    system.close()
    reopened = QualitySystem(settings)
    try:
        assert reopened.state().cards[card.nc_id].status == "confirmed"
    finally:
        reopened.close()


def test_no_signs_on_poor_observation_is_not_conformity(system):
    system.ingest(
        [
            message(),
            message(
                "F1",
                event_type="inspection_reported",
                source_id="vision-final",
                checkpoint_id="CP-FINAL-01",
                checkpoint_kind="final",
                inspection_result="no_defect_signs",
                observation_quality="poor",
            ),
        ],
        received_at=NOW,
    )
    assert system.state().statuses["B-1"] == "not_assessable"


def test_repeated_observation_does_not_double_count(system):
    burr = [{"defect_type": "BURR", "area": "кромка"}]
    system.ingest(
        [
            message(),
            inspection("I1", "defect_signs_found", defects=burr),
            inspection("I2", "defect_signs_found", defects=burr),
        ],
        received_at=NOW,
    )
    metrics = system.state().metrics["defects"]
    assert metrics["signals_total"] == 2
    assert metrics["nonconformances_total"] == 1
