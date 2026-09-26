"""
Представления ответов API: из состояния ядра в словари для интерфейса.

Правило одно — происхождение каждого значения видно. Исходное сообщение показывается
как пришло, оценка системы подписана как оценка, решение — с автором. Время операции
несёт пометку, передано ли оно источником или вычислено системой. Материал, которого
нет, показывается отсутствующим, а не заглушкой.
"""

from __future__ import annotations

from datetime import datetime

from zero_defect.config import DATA_DIR
from zero_defect.history.projection import MACHINE_DEVIATIONS, Observation, Run
from zero_defect.ingest.model import SourceEvent
from zero_defect.lines.catalog import title_of
from zero_defect.quality.nonconformance import (
    CAUSE_ACTION,
    TRANSITIONS,
    Nonconformance,
    check_decision,
    recheck_outcome,
)
from zero_defect.security.auth import Principal
from zero_defect.service import QualitySystem, State

MEDIA_DIR = DATA_DIR / "media"

# Порядок тяжести для очереди контролёра: сначала критичные.
SEVERITY = {"critical": 0, "major": 1, "minor": 2}

STATION_TITLES = {
    "ST-INC": "Входной контроль",
    "ST-MILL": "Механообработка",
    "ST-WELD": "Сварка",
    "ST-ASM": "Сборка",
    "ST-QC": "ОТК, финальный контроль",
}


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def event_view(event: SourceEvent) -> dict:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "occurred_at": _iso(event.occurred_at),
        "received_at": _iso(event.received_at),
        "source_id": event.source_id,
        "sequence_no": event.sequence_no,
        "item_id": event.item_id,
        "station_id": event.station_id,
        "operation_run_id": event.operation_run_id,
        "operator_id": event.operator_id,
        "equipment_id": event.equipment_id,
        "inspection_result": event.inspection_result,
        "checkpoint_id": event.checkpoint_id,
        "machine_state": event.machine_state,
        "action_type": event.action_type,
        "message": event.message or event.details,
        "defects": [defect.__dict__ for defect in event.defects],
        "flags": list(event.flags),
    }


def run_view(run: Run, state: State) -> dict:
    events = state.history.events_by_id
    return {
        "run_id": run.run_id,
        "item_id": run.item_id,
        "operation_id": run.operation_id,
        "station_id": run.station_id,
        "operator_id": run.operator_id,
        "equipment_id": run.equipment_id,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "status": run.status,
        "is_rework": run.is_rework,
        "previous_run_id": run.previous_run_id,
        "rework_reason": run.rework_reason,
        "identification": run.identification,
        "durations": {
            "station_time_s": run.station_time_s,
            "active_time_s": run.active_time_s,
            "paused_s": run.paused_s,
            "origin": "system",
            "reported_s": run.reported_duration_s,
            "reported_meaning": run.reported_duration_meaning,
            "reported_origin": "reported" if run.reported_duration_s is not None else None,
        },
        "shift": {"id": run.shift_id, "origin": run.shift_origin},
        "machine_events": [event_view(events[event_id]) for event_id in run.machine_event_ids],
        "actions": [event_view(events[event_id]) for event_id in run.action_event_ids],
    }


def observation_view(observation: Observation) -> dict:
    event = observation.event
    return {
        **event_view(event),
        "checkpoint_kind": event.checkpoint_kind,
        "confidence": event.confidence,
        "observation_quality": event.observation_quality,
        "analyzer_version": event.analyzer_version,
        "effective_result": observation.effective_result,
        "reliable": observation.reliable,
        "reliability_note": observation.reliability_note,
    }


def evidence_view(event: SourceEvent) -> list[dict]:
    result = []
    for ref in event.evidence:
        local = MEDIA_DIR / ref.uri.split("/")[-1]
        available = local.exists()
        result.append(
            {
                "uri": ref.uri,
                "kind": ref.kind,
                "captured_at": _iso(ref.captured_at),
                "checkpoint_id": ref.checkpoint_id,
                "item_id": event.item_id,
                "source_id": event.source_id,
                "available": available,
                "note": None
                if available
                else "материал в этом контуре отсутствует; ссылка сохранена как пришла",
            }
        )
    return result


def nc_summary(card: Nonconformance) -> dict:
    first = card.first_signal.observation.event
    assessment = card.assessment or {}
    return {
        "nc_id": card.nc_id,
        "item_id": card.item_id,
        "defect_type": card.defect_type,
        "defect_title": title_of(card.defect_type),
        "area": card.area,
        "severity": card.severity,
        "status": card.status,
        "signals": len(card.signals),
        "first_detected_at": _iso(card.first_detected_at),
        "checkpoint_id": first.checkpoint_id,
        "station_id": first.station_id,
        "line_id": first.line_id,
        "stage": assessment.get("stage"),
        "stage_confidence": assessment.get("stage_confidence"),
        "presumed_cause": assessment.get("presumed_cause"),
        "confirmed_cause": card.confirmed_cause,
    }


def allowed_actions(card: Nonconformance, principal: Principal) -> list[str]:
    actions = []
    if principal.can("decide"):
        actions += [action for action in TRANSITIONS if check_decision(card, action, None) is None]
    if (principal.can("set_cause") or principal.can("decide")) and check_decision(
        card, CAUSE_ACTION, "other"
    ) is None:
        actions.append(CAUSE_ACTION)
    return actions


def nc_card(card: Nonconformance, system: QualitySystem, principal: Principal) -> dict:
    state = system.snapshot()
    signals = []
    evidence = []
    for signal in card.signals:
        observation = signal.observation
        signals.append(
            {
                **observation_view(observation),
                "defect": observation.event.defects[signal.defect_index].__dict__,
                "raw": system.raw_event(observation.event.event_id),
            }
        )
        evidence += evidence_view(observation.event)
    return {
        **nc_summary(card),
        "signals_detail": signals,
        "assessment": card.assessment,
        "decisions": [decision.as_dict() for decision in card.decisions],
        "ignored_decisions": [decision.as_dict() for decision in card.ignored_decisions],
        "recheck_outcome": recheck_outcome(card, state.history),
        "signals_after_last_decision": card.signals_after_last_decision(),
        "evidence": evidence,
        "candidate_runs": [
            run_view(state.history.runs[run_id], state)
            for run_id in (card.assessment or {}).get("candidate_run_ids", [])
            if run_id in state.history.runs
        ],
        "allowed_actions": allowed_actions(card, principal),
    }


def item_view(item_id: str, system: QualitySystem) -> dict | None:
    state = system.snapshot()
    history = state.history
    item = history.items.get(item_id)
    if item is None:
        return None
    scope = {item_id, *item.components}
    runs = [history.runs[run_id] for run_id in item.run_ids]
    related_events = [
        event
        for event in history.events
        if event.item_id == item_id or event.component_item_id == item_id
    ]
    for run in runs:
        related_events += [history.events_by_id[event_id] for event_id in run.machine_event_ids]
        related_events += [history.events_by_id[event_id] for event_id in run.action_event_ids]
    unique = {event.event_id: event for event in related_events}
    timeline = sorted(unique.values(), key=lambda event: (event.occurred_at, event.ledger_seq))
    return {
        "item_id": item_id,
        "item_type_id": item.item_type_id,
        "status": state.statuses.get(item_id),
        "registered": item.registered,
        "registered_at": _iso(item.registered_at),
        "origin": item.origin,
        "work_order_id": item.work_order_id,
        "line_id": item.line_id,
        "parent_id": item.parent_id,
        "components": [
            {"item_id": component, "status": state.statuses.get(component)}
            for component in item.components
        ],
        "runs": [run_view(run, state) for run in sorted(runs, key=_run_order)],
        "observations": [observation_view(observation) for observation in item.observations],
        "nonconformances": [
            nc_summary(card) for card in state.cards.values() if card.item_id in scope
        ],
        "timeline": [event_view(event) for event in timeline],
    }


def _run_order(run: Run) -> tuple[bool, float]:
    moment = run.started_at or run.finished_at
    return (moment is None, moment.timestamp() if moment else 0.0)


def line_view(system: QualitySystem) -> dict:
    state = system.snapshot()
    history = state.history
    stations: dict[str, dict] = {}
    for station_id, title in STATION_TITLES.items():
        stations[station_id] = {
            "station_id": station_id,
            "title": title,
            "in_progress": [],
            "completed_runs": 0,
            "open_nonconformances": 0,
        }
    for run in history.runs.values():
        target = stations.setdefault(
            run.station_id or "—",
            {
                "station_id": run.station_id,
                "title": run.station_id,
                "in_progress": [],
                "completed_runs": 0,
                "open_nonconformances": 0,
            },
        )
        if run.status == "in_progress":
            target["in_progress"].append(run_view(run, state))
        elif run.status == "completed":
            target["completed_runs"] += 1
    for card in state.cards.values():
        station = card.first_signal.observation.event.station_id
        if card.is_open and station in stations:
            stations[station]["open_nonconformances"] += 1
    equipment = {}
    for equipment_id, events in history.machine_events.items():
        last = events[-1]
        equipment[equipment_id] = {
            "equipment_id": equipment_id,
            "state": last.machine_state,
            "at": _iso(last.occurred_at),
            "message": last.message,
            "deviations": sum(1 for event in events if event.machine_state in MACHINE_DEVIATIONS),
        }
    recent = sorted(history.events, key=lambda event: event.received_at, reverse=True)[:40]
    return {
        "stations": list(stations.values()),
        "equipment": list(equipment.values()),
        "counters": {
            "items": len(history.items),
            "in_progress_runs": sum(
                1 for run in history.runs.values() if run.status == "in_progress"
            ),
            "open_nonconformances": sum(1 for card in state.cards.values() if card.is_open),
            "quarantine": len(state.quarantine),
            "late_events": sum(1 for event in history.events if "late" in event.flags),
            "sources_with_gaps": len(history.source_gaps()),
            "unreadable_records": len(state.unreadable),
        },
        "source_gaps": history.source_gaps(),
        "recent_events": [event_view(event) for event in recent],
    }
