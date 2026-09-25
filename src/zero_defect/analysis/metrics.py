"""
Производственная аналитика по качеству, длительности операций и повторной обработке.

Два правила держат показатели честными. Дефекты и изделия с дефектами считаются
раздельно: одно изделие с тремя дефектами — это три дефекта, но одно изделие. И каждое
значение времени помечено происхождением: передано источником (reported) или
вычислено системой по отметкам событий (system), потому что это разные величины.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median

from zero_defect.history.projection import History
from zero_defect.quality.nonconformance import CONFIRMED_STATUSES, REJECTED, Nonconformance


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean_s": None, "median_s": None}
    return {
        "count": len(values),
        "mean_s": round(mean(values), 1),
        "median_s": round(median(values), 1),
    }


def _durations_by(history: History, key: str) -> dict:
    groups: dict[str, list[float]] = defaultdict(list)
    reported: dict[str, list[float]] = defaultdict(list)
    for run in history.runs.values():
        group = getattr(run, key) or "не указано"
        if run.active_time_s is not None:
            groups[group].append(run.active_time_s)
        if run.reported_duration_s is not None:
            reported[group].append(run.reported_duration_s)
    return {
        group: {
            "system_active": _stats(groups.get(group, [])),
            "reported": _stats(reported.get(group, [])),
        }
        for group in sorted(set(groups) | set(reported))
    }


def compute(history: History, cards: dict[str, Nonconformance], statuses: dict[str, str]) -> dict:
    """Все показатели одним словарём; ключи стабильны, на них опираются сценарии."""

    inspected = {obs.event.item_id for obs in history.observations}
    confirmed = [card for card in cards.values() if card.status in CONFIRMED_STATUSES]
    rejected = [card for card in cards.values() if card.status == REJECTED]
    pending = [card for card in cards.values() if card.is_open]
    signals_total = sum(len(card.signals) for card in cards.values())

    def by(attribute: str, subset: list[Nonconformance]) -> dict[str, int]:
        counter: Counter = Counter()
        for card in subset:
            event = card.first_signal.observation.event
            value = card.defect_type if attribute == "defect_type" else getattr(event, attribute)
            counter[value or "не указано"] += 1
        return dict(sorted(counter.items()))

    cause_confirmed = Counter(card.confirmed_cause for card in confirmed if card.confirmed_cause)
    hypotheses = Counter(
        card.assessment["presumed_cause"]
        for card in cards.values()
        if card.assessment and not card.confirmed_cause and card.status != REJECTED
    )

    reworks = [run for run in history.runs.values() if run.is_rework]
    unfinished = [run for run in history.runs.values() if run.status == "in_progress"]

    # Сопоставимые работы — выполнения одной и той же операции техпроцесса. Ошибка
    # засчитывается исполнителю только подтверждённая: предположения системы сюда не
    # попадают, иначе показатель наказывал бы за гипотезу.
    comparable: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(dict))
    runs_per: Counter = Counter()
    for run in history.runs.values():
        if run.operation_id and run.operator_id:
            runs_per[(run.operation_id, run.operator_id)] += 1
    errors_per: Counter = Counter()
    for card in confirmed:
        if card.confirmed_cause != "operator_error" or not card.assessment:
            continue
        for run_id in card.assessment.get("candidate_run_ids", []):
            run = history.runs.get(run_id)
            if run and run.operation_id and run.operator_id:
                errors_per[(run.operation_id, run.operator_id)] += 1
    for (operation, operator), runs in sorted(runs_per.items()):
        errors = errors_per.get((operation, operator), 0)
        comparable[operation][operator] = {
            "runs": runs,
            "confirmed_errors": errors,
            "error_rate": round(errors / runs, 3),
        }

    # Прохождение контроля с первого раза: изделие дошло до финального контроля, у него и
    # его компонентов не было ни повторных операций, ни подтверждённых несоответствий.
    finals = {
        item_id
        for item_id, item in history.items.items()
        if any(obs.event.checkpoint_kind == "final" for obs in item.observations)
    }
    first_pass = [item_id for item_id in finals if statuses.get(item_id) == "conforming"]
    first_pass = [
        item_id
        for item_id in first_pass
        if not any(
            history.runs[run_id].is_rework
            for member in (item_id, *history.items[item_id].components)
            for run_id in (history.items[member].run_ids if member in history.items else [])
        )
    ]

    delays = []
    for card in cards.values():
        if not card.assessment or len(card.assessment.get("candidate_run_ids", [])) != 1:
            continue
        run = history.runs.get(card.assessment["candidate_run_ids"][0])
        if run and run.finished_at:
            delays.append((card.first_detected_at - run.finished_at).total_seconds())

    waiting = []
    for item in history.items.values():
        runs = sorted(
            (history.runs[run_id] for run_id in item.run_ids if history.runs[run_id].started_at),
            key=lambda run: run.started_at,
        )
        for before, after in zip(runs, runs[1:], strict=False):
            if before.finished_at and after.started_at > before.finished_at:
                waiting.append((after.started_at - before.finished_at).total_seconds())

    status_counts = Counter(statuses.values())
    return {
        "items": {
            "total": len(history.items),
            "inspected": len(inspected),
            "with_confirmed_nonconformance": len({card.item_id for card in confirmed}),
            "with_open_signals": len({card.item_id for card in pending}),
            "by_status": dict(sorted(status_counts.items())),
        },
        "defects": {
            "signals_total": signals_total,
            "nonconformances_total": len(cards),
            "confirmed": len(confirmed),
            "rejected": len(rejected),
            "pending_review": len(pending),
            "by_type": by("defect_type", list(cards.values())),
            "confirmed_by_type": by("defect_type", confirmed),
            "by_line": by("line_id", list(cards.values())),
            "by_station": by("station_id", list(cards.values())),
        },
        "causes": {
            "established": sum(1 for card in confirmed if card.confirmed_cause),
            "not_established": sum(1 for card in confirmed if not card.confirmed_cause),
            "confirmed_by_category": dict(sorted(cause_confirmed.items())),
            "incoming_defects_confirmed": cause_confirmed.get("incoming_defect", 0),
            "equipment_problems_confirmed": cause_confirmed.get("equipment_problem", 0),
            "operator_errors_confirmed": cause_confirmed.get("operator_error", 0),
            "hypotheses": dict(sorted(hypotheses.items())),
        },
        "operations": {
            "runs_total": len(history.runs),
            "unfinished": len(unfinished),
            "unfinished_run_ids": sorted(run.run_id for run in unfinished),
            "rework_runs": len(reworks),
            "items_with_rework": len({run.item_id for run in reworks}),
            "duration_origin": "system_active — вычислено системой по отметкам начала, "
            "завершения и пауз; reported — передано источником",
            "duration_by_station": _durations_by(history, "station_id"),
            "duration_by_operator": _durations_by(history, "operator_id"),
            "duration_by_shift": _durations_by(history, "shift_id"),
            "duration_by_item": _durations_by(history, "item_id"),
        },
        "comparable_work": {
            operation: dict(rows) for operation, rows in sorted(comparable.items())
        },
        "extra": {
            "items_with_final_inspection": len(finals),
            "first_pass_yield": round(len(first_pass) / len(finals), 3) if finals else None,
            "detection_delay_s": _stats(delays),
            "waiting_between_operations_s": _stats(waiting),
        },
    }
