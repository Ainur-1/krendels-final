"""
Разбор обстоятельств несоответствия (ErrorAnalysis).

Система не выносит приговор, а раскладывает, что известно: на каком этапе признак
появился, что происходило с оборудованием и какие были действия, что ещё могло
привести к тому же, и каких сведений не хватает. Правила, которых мы держимся:

- этап определяется по последнему достоверному чистому контролю до обнаружения:
  дефект появился между ним и обнаружением, и никак не раньше;
- контроль с плохим наблюдением этапа не ограничивает — он честно попадает в список
  недостающих сведений, а не молча считается «годно»;
- оператор операции — участник, а не виновник. Ошибка оператора бывает только
  подтверждённой человеком, система её не предполагает;
- гипотеза о проблеме оборудования появляется только при отклонении станка в окне
  операции, и она остаётся гипотезой до решения технолога.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zero_defect.config import Settings
from zero_defect.history.projection import MACHINE_DEVIATIONS, History, Observation, Run
from zero_defect.quality.nonconformance import Nonconformance

# Этап возникновения.
STAGE_INCOMING = "incoming"
STAGE_OPERATION = "operation"
STAGE_BETWEEN_CHECKS = "between_checks"
STAGE_UNKNOWN = "unknown"

# Насколько система уверена в этапе. Это не вероятность, а описание оснований.
STRONG = "strong"
MODERATE = "moderate"
INSUFFICIENT = "insufficient"

# Действия оператора, которые относятся к обстоятельствам, но не к вине.
NOTABLE_ACTIONS = ("check_skipped", "mode_change", "manual_decision", "tool_change")


@dataclass
class Assessment:
    """Итог разбора: этап, основания, альтернативы и недостающие сведения."""

    stage: str
    stage_confidence: str
    presumed_cause: str
    candidate_run_ids: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    participants: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "stage": self.stage,
            "stage_confidence": self.stage_confidence,
            "presumed_cause": self.presumed_cause,
            "candidate_run_ids": self.candidate_run_ids,
            "evidence": self.evidence,
            "alternatives": self.alternatives,
            "missing": self.missing,
            "participants": self.participants,
            "operator_blamed": False,
        }


def _describe_run(run: Run) -> str:
    where = run.station_id or "участок не указан"
    return f"{run.operation_id or 'операция'} ({run.run_id}, {where})"


def _checkpoint(observation: Observation, settings: Settings) -> str:
    event = observation.event
    # Время — в часовом поясе предприятия: источники вправе присылать его в любом поясе,
    # а текст разбора читают люди на участке.
    local = event.occurred_at.astimezone(settings.timezone)
    return f"{event.checkpoint_id} ({event.checkpoint_kind}) в {local:%d.%m %H:%M}"


def assess(card: Nonconformance, history: History, settings: Settings) -> Assessment:
    first = card.first_signal.observation
    detected_at = first.occurred_at
    event = first.event

    if event.checkpoint_kind == "incoming":
        result = Assessment(
            stage=STAGE_INCOMING,
            stage_confidence=STRONG if first.reliable else MODERATE,
            presumed_cause="incoming_defect",
        )
        result.evidence.append(
            {
                "kind": "incoming_inspection",
                "text": f"признак зафиксирован на входном контроле {_checkpoint(first, settings)}, "
                "до любых операций на предприятии",
                "event_ids": [event.event_id],
            }
        )
        later_runs = [
            run
            for run in history.runs.values()
            if run.item_id == card.item_id and run.started_at and run.started_at > detected_at
        ]
        if later_runs:
            result.evidence.append(
                {
                    "kind": "later_operations_excluded",
                    "text": "операции после входного контроля источником не считаются: "
                    "признак был до них — " + ", ".join(_describe_run(run) for run in later_runs),
                    "event_ids": [],
                }
            )
        if not first.reliable:
            result.missing.append(
                f"наблюдение на входном контроле недостоверно ({first.reliability_note}); "
                "нужна повторная проверка"
            )
            result.alternatives.append("ложное срабатывание анализатора на входном контроле")
        return result

    scope = {card.item_id, *history.ancestors(card.item_id)}
    prior = sorted(
        (
            obs
            for obs in history.observations
            if obs.event.item_id in scope and obs.occurred_at < detected_at
        ),
        key=lambda obs: obs.occurred_at,
    )
    clean = [obs for obs in prior if obs.effective_result == "no_defect_signs" and obs.reliable]
    boundary = clean[-1] if clean else None
    unreliable_before = [
        obs
        for obs in prior
        if not obs.reliable and (boundary is None or obs.occurred_at > boundary.occurred_at)
    ]
    runs_before = [
        run
        for run in history.runs.values()
        if run.item_id in scope and run.started_at is not None and run.started_at < detected_at
    ]
    if boundary is not None:
        candidates = [
            run for run in runs_before if (run.finished_at or detected_at) > boundary.occurred_at
        ]
    else:
        candidates = runs_before
    candidates.sort(key=lambda run: run.started_at)

    if boundary is None:
        result = Assessment(STAGE_UNKNOWN, INSUFFICIENT, "not_established")
        result.candidate_run_ids = [run.run_id for run in candidates]
        result.alternatives.append(
            "входной брак: достоверного входного контроля нет, исключить его нельзя"
        )
        for run in candidates:
            result.alternatives.append(f"возникновение на операции {_describe_run(run)}")
        result.missing.append("нет ни одного достоверного контроля без признаков до обнаружения")
    elif not candidates:
        result = Assessment(STAGE_BETWEEN_CHECKS, MODERATE, "not_established")
        result.evidence.append(
            {
                "kind": "clean_boundary",
                "text": "последний достоверный контроль без признаков — "
                f"{_checkpoint(boundary, settings)}; "
                "операций между ним и обнаружением не было",
                "event_ids": [boundary.event.event_id],
            }
        )
        result.alternatives.append("повреждение при перемещении или хранении между проверками")
    else:
        confidence = STRONG if len(candidates) == 1 else MODERATE
        result = Assessment(STAGE_OPERATION, confidence, "not_established")
        result.candidate_run_ids = [run.run_id for run in candidates]
        result.evidence.append(
            {
                "kind": "clean_boundary",
                "text": "последний достоверный контроль без признаков — "
                f"{_checkpoint(boundary, settings)}; "
                "признак появился после него",
                "event_ids": [boundary.event.event_id],
            }
        )
        names = ", ".join(_describe_run(run) for run in candidates)
        result.evidence.append(
            {
                "kind": "candidate_operations",
                "text": f"между чистым контролем и обнаружением выполнено: {names}",
                "event_ids": [event_id for run in candidates for event_id in run.event_ids],
            }
        )

    for obs in unreliable_before:
        result.missing.append(
            f"контроль {_checkpoint(obs, settings)} не позволяет судить о дефекте: "
            f"{obs.reliability_note}"
        )

    deviations = []
    for run in candidates:
        for event_id in run.machine_event_ids:
            machine = history.events_by_id[event_id]
            if machine.machine_state in MACHINE_DEVIATIONS:
                deviations.append((run, machine))
        for event_id in run.action_event_ids:
            action = history.events_by_id[event_id]
            if action.action_type in NOTABLE_ACTIONS:
                result.evidence.append(
                    {
                        "kind": "operator_action",
                        "text": f"в ходе {_describe_run(run)} зафиксировано действие "
                        f"{action.action_type}: {action.details or 'без пояснения'}. "
                        "Это обстоятельство, а не вывод о вине",
                        "event_ids": [event_id],
                    }
                )
        if run.identification == "unreliable":
            result.missing.append(f"изделие на операции {_describe_run(run)} опознано ненадёжно")
        if run.status == "in_progress":
            result.missing.append(f"нет события завершения операции {_describe_run(run)}")
        if run.operator_id:
            result.participants.append(
                {
                    "operator_id": run.operator_id,
                    "run_id": run.run_id,
                    "operation_id": run.operation_id,
                    "note": "участник операции; вывод о вине без решения контролёра не делается",
                }
            )

    for run, machine in deviations:
        result.evidence.append(
            {
                "kind": "machine_deviation",
                "text": f"оборудование {machine.equipment_id} "
                f"в окне операции {_describe_run(run)}: "
                f"{machine.machine_state}, {machine.message or 'без описания'}",
                "event_ids": [machine.event_id],
            }
        )
    if deviations and result.stage == STAGE_OPERATION:
        result.presumed_cause = "equipment_problem"
    elif result.stage == STAGE_OPERATION:
        result.alternatives.append(
            "режим оборудования: отклонений в журнале станка нет, но журнал мог их не отразить"
        )
        result.alternatives.append("свойства материала или заготовки, не выявленные ранее")

    if result.stage == STAGE_OPERATION and candidates:
        detection_station = event.station_id
        last_station = candidates[-1].station_id
        if detection_station and last_station and detection_station != last_station:
            result.alternatives.append(
                f"повреждение при перемещении {last_station} → {detection_station}: "
                "данных о перемещении нет"
            )
    if not first.reliable:
        result.alternatives.append(f"ложное срабатывание анализатора ({first.reliability_note})")
    return result
