"""
Линия в разрезе этапов: живое состояние графа, статистика этапа, маршрут изделия и
экономика.

Всё считается из той же истории, что и остальная система, — эмулятор здесь ничего не
подсказывает. Событие относится к этапу по участку и операции техпроцесса (операции)
или по контрольной точке (контроль), поэтому те же представления работают и для
настоящей линии, и для проверочных сценариев.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

from zero_defect.history.projection import MACHINE_DEVIATIONS, Observation, Run, shift_for
from zero_defect.lines.catalog import title_of
from zero_defect.lines.model import LineConfig, Node
from zero_defect.quality.nonconformance import CONFIRMED_STATUSES
from zero_defect.service import State, unassessable_resolution

STATUS_ORDER = (
    "pending",
    "passed",
    "processing",
    "reworked",
    "not_assessable",
    "possible_origin",
    "defect_detected",
    "defect_origin",
)


@dataclass
class Filters:
    """Фильтры представлений по этапам: тип изделия, период, смена."""

    item_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    shift: str | None = None


@dataclass
class LineIndex:
    """События линии, разложенные по этапам. Строится один раз на состояние."""

    config: LineConfig
    item_types: dict[str, str | None]
    item_line: dict[str, str | None]
    runs: dict[str, list[Run]] = field(default_factory=lambda: defaultdict(list))
    observations: dict[str, list[Observation]] = field(default_factory=lambda: defaultdict(list))
    detected_at: dict[str, str] = field(default_factory=dict)
    origins: dict[str, tuple[list[str], bool]] = field(default_factory=dict)
    run_node: dict[str, str] = field(default_factory=dict)


def item_line(item_id: str, history) -> str | None:
    """Линия изделия: своя или линия изделия, в которое оно собрано."""

    seen = set()
    current = history.items.get(item_id)
    while current is not None and item_id not in seen:
        seen.add(item_id)
        if current.line_id:
            return current.line_id
        item_id = current.parent_id
        current = history.items.get(item_id) if item_id else None
    return None


def build_index(config: LineConfig, state: State) -> LineIndex:
    history = state.history
    item_types = {item_id: item.item_type_id for item_id, item in history.items.items()}
    lines_of = {item_id: item_line(item_id, history) for item_id in history.items}
    index = LineIndex(config, item_types, lines_of)
    for run in history.runs.values():
        line = run.line_id or lines_of.get(run.item_id)
        if line != config.line_id:
            continue
        node = config.operation_node(run.station_id, run.operation_id)
        if node is not None:
            index.runs[node.node_id].append(run)
            index.run_node[run.run_id] = node.node_id
    for observation in history.observations:
        event = observation.event
        if (event.line_id or lines_of.get(event.item_id)) != config.line_id:
            continue
        node = config.inspection_node(event.checkpoint_id, item_types.get(event.item_id))
        if node is not None:
            index.observations[node.node_id].append(observation)
    for card in state.cards.values():
        event = card.first_signal.observation.event
        if (event.line_id or lines_of.get(card.item_id)) != config.line_id:
            continue
        node = config.inspection_node(event.checkpoint_id, item_types.get(event.item_id))
        if node is not None:
            index.detected_at[card.nc_id] = node.node_id
        assessment = card.assessment or {}
        stage = assessment.get("stage")
        if stage == "incoming" and node is not None:
            index.origins[card.nc_id] = ([node.node_id], True)
            continue
        nodes = []
        for run_id in assessment.get("candidate_run_ids", []):
            run_node = index.run_node.get(run_id)
            if run_node and run_node not in nodes:
                nodes.append(run_node)
        strong = stage == "operation" and assessment.get("stage_confidence") == "strong"
        if nodes:
            index.origins[card.nc_id] = (nodes, strong and len(nodes) == 1)
    return index


def _keep(
    moment: datetime | None,
    item_id: str | None,
    shift: str | None,
    index: LineIndex,
    filters: Filters,
    settings,
) -> bool:
    if filters.item_type and index.item_types.get(item_id) != filters.item_type:
        return False
    if moment is not None:
        if filters.since and moment < filters.since:
            return False
        if filters.until and moment > filters.until:
            return False
        if filters.shift and (shift or shift_for(moment, settings)) != filters.shift:
            return False
    return True


def _median(values: list[float]) -> float | None:
    return round(median(values), 1) if values else None


def stage_stats(index: LineIndex, node: Node, state: State, settings, filters: Filters) -> dict:
    """Статистика одного этапа за выбранный срез."""

    cards = state.cards
    base = {
        "node_id": node.node_id,
        "title": node.title,
        "kind": node.kind,
        "station_id": node.station_id,
        "equipment_id": node.equipment_id,
        "checkpoint_id": node.checkpoint_id,
        # Норма длительности нужна интерфейсу, чтобы оценить медиану: в норме или нет.
        "norm_duration_s": node.duration_s,
    }
    originated = [
        nc
        for nc, (nodes, _) in index.origins.items()
        if node.node_id in nodes
        and nc in cards
        and _keep(cards[nc].first_detected_at, cards[nc].item_id, None, index, filters, settings)
    ]
    strong = [nc for nc in originated if index.origins[nc][1]]
    confirmed_here = [nc for nc in originated if cards[nc].status in CONFIRMED_STATUSES]
    if node.kind == "operation":
        runs = [
            run
            for run in index.runs.get(node.node_id, [])
            if _keep(run.started_at, run.item_id, run.shift_id, index, filters, settings)
        ]
        by_operator: dict[str, dict] = defaultdict(lambda: {"runs": 0, "confirmed_errors": 0})
        for run in runs:
            if run.operator_id:
                by_operator[run.operator_id]["runs"] += 1
        for nc in confirmed_here:
            if cards[nc].confirmed_cause == "operator_error":
                for run_id in cards[nc].assessment.get("candidate_run_ids", []):
                    run = state.history.runs.get(run_id)
                    if run and run.operator_id and index.run_node.get(run_id) == node.node_id:
                        by_operator[run.operator_id]["confirmed_errors"] += 1
        deviations = 0
        if node.equipment_id:
            deviations = sum(
                1
                for event in state.history.machine_events.get(node.equipment_id, [])
                if event.machine_state in MACHINE_DEVIATIONS
                and _keep(
                    event.occurred_at,
                    None,
                    None,
                    index,
                    Filters(since=filters.since, until=filters.until, shift=filters.shift),
                    settings,
                )
            )
        active = [
            run.active_time_s for run in runs if run.active_time_s is not None and not run.is_rework
        ]
        reported = [
            run.reported_duration_s
            for run in runs
            if run.reported_duration_s is not None and not run.is_rework
        ]
        return {
            **base,
            "items": len({run.item_id for run in runs}),
            "runs": len(runs),
            "completed": sum(1 for run in runs if run.status == "completed"),
            "in_progress": sum(1 for run in runs if run.status == "in_progress"),
            "rework_runs": sum(1 for run in runs if run.is_rework),
            "median_active_s": _median(active),
            "median_reported_s": _median(reported),
            "deviations": deviations,
            "defects_originated": len(originated),
            "defects_originated_strong": len(strong),
            "defects_confirmed": len(confirmed_here),
            "defect_types": dict(Counter(cards[nc].defect_type for nc in originated)),
            "by_operator": dict(sorted(by_operator.items())),
        }
    observations = [
        obs
        for obs in index.observations.get(node.node_id, [])
        if _keep(obs.occurred_at, obs.event.item_id, obs.event.shift_id, index, filters, settings)
    ]
    results = Counter(obs.effective_result for obs in observations)
    detected = [
        nc
        for nc, where in index.detected_at.items()
        if where == node.node_id
        and _keep(cards[nc].first_detected_at, cards[nc].item_id, None, index, filters, settings)
    ]
    checks = len(observations)
    return {
        **base,
        "checkpoint_kind": node.checkpoint_kind,
        "items": len({obs.event.item_id for obs in observations}),
        "checks": checks,
        "clean": results.get("no_defect_signs", 0),
        "found": results.get("defect_signs_found", 0),
        "not_assessable": results.get("not_assessable", 0),
        "unreliable": sum(1 for obs in observations if not obs.reliable),
        "pass_rate": round(results.get("no_defect_signs", 0) / checks, 3) if checks else None,
        "first_detections": len(detected),
        "open_detections": sum(1 for nc in detected if cards[nc].is_open),
        "defects_originated": len(originated),
        "defect_types": dict(Counter(cards[nc].defect_type for nc in detected)),
    }


def stage_table(index: LineIndex, state: State, settings, filters: Filters) -> list[dict]:
    return [stage_stats(index, node, state, settings, filters) for node in index.config.order()]


def stage_items(
    index: LineIndex, node: Node, state: State, settings, filters: Filters, limit: int = 60
) -> list[dict]:
    """Изделия, прошедшие этап, с итогом на нём — для точечного изучения."""

    rows = []
    if node.kind == "operation":
        for run in index.runs.get(node.node_id, []):
            if not _keep(run.started_at, run.item_id, run.shift_id, index, filters, settings):
                continue
            rows.append(
                {
                    "item_id": run.item_id,
                    "item_type": index.item_types.get(run.item_id),
                    "at": run.started_at.isoformat() if run.started_at else None,
                    "outcome": "reworked" if run.is_rework else run.status,
                    "operator_id": run.operator_id,
                    "status": state.statuses.get(run.item_id),
                }
            )
    else:
        for obs in index.observations.get(node.node_id, []):
            if not _keep(
                obs.occurred_at, obs.event.item_id, obs.event.shift_id, index, filters, settings
            ):
                continue
            rows.append(
                {
                    "item_id": obs.event.item_id,
                    "item_type": index.item_types.get(obs.event.item_id),
                    "at": obs.occurred_at.isoformat(),
                    "outcome": obs.effective_result,
                    "operator_id": None,
                    "status": state.statuses.get(obs.event.item_id),
                }
            )
    rows.sort(key=lambda row: row["at"] or "", reverse=True)
    return rows[:limit]


def current_stage(index: LineIndex, item_id: str, state: State) -> str | None:
    """Этап, на котором изделие было в последний раз."""

    history = state.history
    item = history.items.get(item_id)
    if item is None:
        return None
    best: tuple[datetime, str] | None = None
    for run_id in item.run_ids:
        node = index.run_node.get(run_id)
        run = history.runs[run_id]
        moment = run.finished_at or run.started_at
        if node and moment and (best is None or moment > best[0]):
            best = (moment, node)
    for obs in item.observations:
        node = index.config.inspection_node(obs.event.checkpoint_id, index.item_types.get(item_id))
        if node and (best is None or obs.occurred_at > best[0]):
            best = (obs.occurred_at, node.node_id)
    if item.parent_id:
        parent = current_stage(index, item.parent_id, state)
        if parent:
            return parent
    return best[1] if best else None


def item_route(config: LineConfig, item_type: str | None) -> list[Node]:
    for source in config.sources():
        if source.item_type_id == item_type:
            return config.chain_from(source.node_id)
    for node in config.nodes:
        if node.output_type and node.output_type == item_type:
            return config.chain_from(node.node_id)
    return config.order()


def item_path(index: LineIndex, item_id: str, state: State) -> dict | None:
    """Маршрут изделия A → B → C с итогом на каждом этапе."""

    history = state.history
    item = history.items.get(item_id)
    if item is None:
        return None
    config = index.config
    scope = [item_id]
    if item.parent_id:
        scope.append(item.parent_id)
    scope += item.components
    route = item_route(config, item.item_type_id)
    nodes = []
    related_cards = [
        card for card in state.cards.values() if card.item_id in {item_id, *item.components}
    ]
    for node in route:
        entry = {
            "node_id": node.node_id,
            "title": node.title,
            "kind": node.kind,
            "station_id": node.station_id,
            "equipment_id": node.equipment_id,
            "status": "pending",
            "runs": [],
            "checks": [],
            "nonconformances": [],
        }
        if node.kind == "operation":
            for run_id, run_node in index.run_node.items():
                run = history.runs[run_id]
                if run_node == node.node_id and run.item_id in scope:
                    entry["runs"].append(
                        {
                            "run_id": run.run_id,
                            "item_id": run.item_id,
                            "operator_id": run.operator_id,
                            "started_at": run.started_at.isoformat() if run.started_at else None,
                            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                            "active_s": run.active_time_s,
                            "rework": run.is_rework,
                            "status": run.status,
                            "deviations": sum(
                                1
                                for eid in run.machine_event_ids
                                if history.events_by_id[eid].machine_state in MACHINE_DEVIATIONS
                            ),
                        }
                    )
            if entry["runs"]:
                statuses = {run["status"] for run in entry["runs"]}
                entry["status"] = "processing" if "in_progress" in statuses else "passed"
                if any(run["rework"] for run in entry["runs"]):
                    entry["status"] = "reworked"
        else:
            for obs in index.observations.get(node.node_id, []):
                if obs.event.item_id in scope:
                    entry["checks"].append(
                        {
                            "event_id": obs.event.event_id,
                            "item_id": obs.event.item_id,
                            "at": obs.occurred_at.isoformat(),
                            "result": obs.effective_result,
                            "confidence": obs.event.confidence,
                            "quality": obs.event.observation_quality,
                            "reliable": obs.reliable,
                            "note": obs.reliability_note,
                            "defects": [defect.defect_type for defect in obs.event.defects],
                        }
                    )
            if entry["checks"]:
                last = entry["checks"][-1]["result"]
                entry["status"] = {
                    "no_defect_signs": "passed",
                    "defect_signs_found": "defect_detected",
                    "not_assessable": "not_assessable",
                }[last]
        nodes.append(entry)
    by_id = {entry["node_id"]: entry for entry in nodes}
    for card in related_cards:
        detected = index.detected_at.get(card.nc_id)
        summary = {
            "nc_id": card.nc_id,
            "defect_type": card.defect_type,
            "status": card.status,
            "item_id": card.item_id,
        }
        if detected in by_id:
            by_id[detected]["nonconformances"].append({**summary, "role": "detected"})
        origin_nodes, strong = index.origins.get(card.nc_id, ([], False))
        for origin in origin_nodes:
            if origin in by_id:
                role = "origin" if strong else "possible_origin"
                by_id[origin]["nonconformances"].append({**summary, "role": role})
                current = by_id[origin]["status"]
                wanted = "defect_origin" if strong else "possible_origin"
                if STATUS_ORDER.index(wanted) > STATUS_ORDER.index(current):
                    by_id[origin]["status"] = wanted
    return {
        "item_id": item_id,
        "item_type_id": item.item_type_id,
        "line_id": config.line_id,
        "status": state.statuses.get(item_id),
        "parent_id": item.parent_id,
        "components": item.components,
        "route": nodes,
        "visits": _visits(index, nodes, related_cards),
    }


def _visits(index: LineIndex, nodes: list[dict], cards: list) -> list[dict]:
    """
    Маршрут изделия в хронологии: каждое прохождение этапа — отдельный шаг.

    Проблемный проход и повторный проход того же этапа показываются раздельно: если
    дефект мог возникнуть на B, а обнаружен на D, цепочка выглядит как B (мог возникнуть)
    → C (мог возникнуть) → D (дефект) → B (доработка) → C (успешно). Этапы, которые
    изделие ещё не прошло, идут в конце.
    """

    visits = []
    for entry in nodes:
        for run in entry["runs"]:
            visits.append(
                {
                    "node_id": entry["node_id"],
                    "title": entry["title"],
                    "kind": "operation",
                    "at": run["started_at"],
                    "rework": run["rework"],
                    "state": run["status"],
                    "event_id": None,
                    "result": None,
                }
            )
        for check in entry["checks"]:
            visits.append(
                {
                    "node_id": entry["node_id"],
                    "title": entry["title"],
                    "kind": "inspection",
                    "at": check["at"],
                    "rework": False,
                    "state": None,
                    "event_id": check["event_id"],
                    "result": check["result"],
                }
            )
    visits = [visit for visit in visits if visit["at"]]
    visits.sort(key=lambda visit: datetime.fromisoformat(visit["at"]))
    detected_first = None
    marks: dict[int, str] = {}
    # Шаг ведёт к своей проблеме: интерфейс по нажатию на шаг открывает её, а не этап.
    # Одно наблюдение может сообщить несколько дефектов — у шага тогда несколько проблем.
    problems: dict[int, list[str]] = defaultdict(list)
    signal_cards: dict[str, list[str]] = defaultdict(list)
    for card in cards:
        for signal in card.signals:
            signal_cards[signal.event_id].append(card.nc_id)
    for card in cards:
        moment = card.first_detected_at
        detected_first = moment if detected_first is None else min(detected_first, moment)
        for position, visit in enumerate(visits):
            if visit["event_id"] == card.first_signal.event_id:
                marks[position] = "defect"
                problems[position].append(card.nc_id)
        origin_nodes, strong = index.origins.get(card.nc_id, ([], False))
        for origin in origin_nodes:
            before = [
                position
                for position, visit in enumerate(visits)
                if visit["node_id"] == origin and datetime.fromisoformat(visit["at"]) < moment
            ]
            if before and marks.get(before[-1]) != "defect":
                marks[before[-1]] = "origin" if strong else "possible_origin"
                problems[before[-1]].append(card.nc_id)
    for position, visit in enumerate(visits):
        linked = problems.get(position) or signal_cards.get(visit["event_id"]) or []
        if visit["result"] == "not_assessable" and not linked:
            linked = [f"NA-{visit['event_id']}"]
        visit["problem_ids"] = list(dict.fromkeys(linked))
        visit["problem_id"] = visit["problem_ids"][0] if linked else None
        if position in marks:
            visit["label"] = marks[position]
        elif visit["result"] == "not_assessable":
            visit["label"] = "not_assessable"
        elif visit["result"] == "defect_signs_found":
            visit["label"] = "defect"
        elif visit["state"] == "in_progress":
            visit["label"] = "processing"
        elif visit["rework"]:
            visit["label"] = "rework"
        elif detected_first and datetime.fromisoformat(visit["at"]) > detected_first:
            visit["label"] = "ok"
        else:
            visit["label"] = "passed"
    seen = {visit["node_id"] for visit in visits}
    for entry in nodes:
        if entry["node_id"] not in seen:
            visits.append(
                {
                    "node_id": entry["node_id"],
                    "title": entry["title"],
                    "kind": entry["kind"],
                    "at": None,
                    "label": "pending",
                    "problem_id": None,
                    "problem_ids": [],
                }
            )
    return visits


def _line_events(index: LineIndex, state: State) -> list:
    line_id = index.config.line_id
    return [
        event
        for event in state.history.events
        if (event.line_id or index.item_line.get(event.item_id)) == line_id
    ]


# Несоответствие перестаёт быть проблемой, когда его закрыли после устранения или
# отклонили как ложный сигнал. Подтверждённое, но не закрытое остаётся проблемой.
RESOLVING_ACTIONS = {"close": "closed", "reject": "rejected"}


def line_problems(index: LineIndex, state: State) -> list[dict]:
    """
    Проблемы линии на момент состояния: несоответствия и наблюдения «оценка невозможна».

    Несоответствие — это брак или его признак, по нему принимает решение контролёр.
    «Оценка невозможна» браком не является (постановка не разрешает считать плохой
    снимок ни годностью, ни браком), но это тоже проблема: изделие нужно проверить ещё
    раз. Она решается следующим достоверным наблюдением того же изделия или допуском по
    ручному контролю — решением мастера или контролёра. Одна проблема —
    одна запись: у несоответствия это место обнаружения, а не каждый возможный этап
    возникновения.
    """

    problems = []
    for nc, node_id in index.detected_at.items():
        card = state.cards[nc]
        resolved_at, resolution = None, None
        if card.status in RESOLVING_ACTIONS.values():
            for decision in reversed(card.decisions):
                if RESOLVING_ACTIONS.get(decision.action) == card.status:
                    resolved_at, resolution = decision.decided_at, card.status
                    break
        problems.append(
            {
                "problem_id": nc,
                "kind": "nonconformance",
                "item_id": card.item_id,
                "node_id": node_id,
                "at": card.first_detected_at,
                "defect_type": card.defect_type,
                "title": title_of(card.defect_type),
                "severity": card.severity or "major",
                "status": card.status,
                "resolved_at": resolved_at,
                "resolution": resolution,
                "note": None,
            }
        )
    for node_id, observations in index.observations.items():
        for obs in observations:
            if obs.effective_result != "not_assessable":
                continue
            problem_id = f"NA-{obs.event.event_id}"
            made = state.unassessable_decisions.get(problem_id, [])
            resolved_at, resolution = unassessable_resolution(state, obs)
            status = "resolved" if resolved_at else "open"
            if not resolved_at and any(item.action == "request_recheck" for item in made):
                status = "recheck_requested"
            problems.append(
                {
                    "problem_id": problem_id,
                    "kind": "not_assessable",
                    "item_id": obs.event.item_id,
                    "node_id": node_id,
                    "at": obs.occurred_at,
                    "defect_type": None,
                    "title": "Оценка невозможна",
                    "severity": None,
                    "status": status,
                    "resolved_at": resolved_at,
                    "resolution": resolution,
                    "note": obs.reliability_note,
                    "decisions": [item.as_dict() for item in made],
                }
            )
    return problems


def problem_payload(problem: dict) -> dict:
    return {
        **problem,
        "at": problem["at"].isoformat(),
        "resolved_at": problem["resolved_at"].isoformat() if problem["resolved_at"] else None,
    }


def _last_moment(run: Run) -> datetime | None:
    return max((m for m in (run.started_at, run.finished_at) if m), default=None)


def passed_items(index: LineIndex, node: Node, since: datetime | None = None) -> set[str]:
    """
    Изделия, прошедшие этап: у операции — по выполнениям, у контроля — по наблюдениям.

    since — начало промежутка шкалы времени: изделие учитывается, если было на этапе
    внутри промежутка. Конец промежутка задаёт само состояние на момент.
    """

    def inside(moment: datetime | None) -> bool:
        return since is None or (moment is not None and moment >= since)

    items = {run.item_id for run in index.runs.get(node.node_id, []) if inside(_last_moment(run))}
    items |= {
        obs.event.item_id
        for obs in index.observations.get(node.node_id, [])
        if inside(obs.occurred_at)
    }
    items.discard(None)
    return items


MACHINE_STATE_TITLES = {
    "warning": "Предупреждение станка",
    "deviation": "Режим станка вне допуска",
    "stopped": "Остановка станка",
}


def machine_problems(index: LineIndex, state: State) -> list[dict]:
    """
    Проблемы оборудования линии: эпизод от выхода станка из нормального режима до его
    возврата в работу.

    Несколько сообщений о сбое подряд — один эпизод, а не по проблеме на сообщение: так
    технолог видит, сколько раз станок выходил из режима, а не как часто он о нём писал.
    Эпизод решён, когда станок снова сообщил о работе.
    """

    problems = []
    for node in index.config.nodes:
        if not node.equipment_id:
            continue
        episode = None
        for event in state.history.machine_events.get(node.equipment_id, []):
            if event.machine_state in MACHINE_DEVIATIONS:
                if episode is None:
                    episode = {
                        "problem_id": f"MF-{event.event_id}",
                        "kind": "machine",
                        "item_id": None,
                        "node_id": node.node_id,
                        "equipment_id": node.equipment_id,
                        "at": event.occurred_at,
                        "defect_type": None,
                        "title": MACHINE_STATE_TITLES.get(event.machine_state, "Сбой станка"),
                        "severity": None,
                        "status": "open",
                        "resolved_at": None,
                        "resolution": None,
                        "note": event.message,
                        "reports": 0,
                    }
                    problems.append(episode)
                episode["reports"] += 1
                episode["note"] = event.message or episode["note"]
            elif event.machine_state == "running" and episode is not None:
                episode.update(status="resolved", resolved_at=event.occurred_at)
                episode["resolution"] = "restored"
                episode = None
    return problems


def live_view(
    index: LineIndex,
    state: State,
    window_s: float = 1800,
    since: datetime | None = None,
    resolved_now: set[str] | None = None,
) -> dict:
    """Состояние графа линии: этапы с тревогами, изделия и их статусы у каждого этапа.

    Изделие отнесено к этапу, на котором было в последний раз. У этапа считается, сколько
    изделий в каком статусе сейчас у него стоит, — так интерфейс рисует счётчики, а не
    вереницу точек. Отдельный список изделий нужен, чтобы анимировать переход изделия
    от этапа к этапу. Состояние бывает и на прошедший момент: тогда state построен из
    событий до этого момента, и «сейчас» — это он.

    У этапа — проблемы, действующие на этот момент (по изделиям и по станку отдельно), и
    изделия, прошедшие этап внутри промежутка шкалы (since — его начало, конец — сам
    момент). Решённость проблемы берётся на настоящее время: resolved_now — проблемы, уже
    решённые к настоящему, и в прошлом они тоже не считаются действующими, даже если
    решение позже конца промежутка.

    Счётчики изделий под этапом — о самом этапе, а не об итоге изделия: «проблема» —
    действующее несоответствие или сигнал, обнаруженный здесь, «оценка невозможна» — здесь
    же, остальные — в работе или прошли этап без проблем. Поэтому красный и жёлтый
    счётчики сходятся с кругом над этапом, а сумма — с числом изделий за промежуток.
    """

    config = index.config
    history = state.history
    line_events = _line_events(index, state)
    latest = max((event.occurred_at for event in line_events), default=None)
    items = []
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if latest is not None:
        recent: dict[str, datetime] = {}
        for event in line_events:
            if event.item_id and (latest - event.occurred_at).total_seconds() <= window_s:
                recent[event.item_id] = max(
                    recent.get(event.item_id, event.occurred_at), event.occurred_at
                )
        for item_id, moment in sorted(recent.items(), key=lambda pair: pair[1], reverse=True)[:250]:
            item = history.items.get(item_id)
            if item is None or (item.parent_id and item.parent_id in recent):
                continue
            node = current_stage(index, item_id, state)
            processing = any(
                history.runs[run_id].status == "in_progress" for run_id in item.run_ids
            )
            status = state.statuses.get(item_id) or "in_progress"
            items.append(
                {
                    "item_id": item_id,
                    "item_type": item.item_type_id,
                    "node_id": node,
                    "status": status,
                    "processing": processing,
                    "last_at": moment.isoformat(),
                }
            )
            counts[node or "__entry"][status] += 1
    done = resolved_now or set()
    open_problems: dict[str, list[dict]] = defaultdict(list)
    for problem in line_problems(index, state) + machine_problems(index, state):
        if problem["resolved_at"] is None and problem["problem_id"] not in done:
            open_problems[problem["node_id"]].append(problem)
    nodes = []
    for node in config.nodes:
        here = open_problems.get(node.node_id, [])
        active = Counter(p["kind"] for p in here)
        flagged = {p["item_id"] for p in here if p["kind"] == "nonconformance"}
        unclear = {p["item_id"] for p in here if p["kind"] == "not_assessable"} - flagged
        passed = passed_items(index, node, since) | flagged | unclear
        by_status: Counter = Counter()
        for item_id in passed:
            if item_id in flagged:
                by_status["problem"] += 1
            elif item_id in unclear:
                by_status["not_assessable"] += 1
            elif (state.statuses.get(item_id) or "in_progress") == "in_progress":
                by_status["in_progress"] += 1
            else:
                by_status["ok"] += 1
        detected = [nc for nc, where in index.detected_at.items() if where == node.node_id]
        open_here = [nc for nc in detected if state.cards[nc].is_open]
        originated = [nc for nc, (where, _) in index.origins.items() if node.node_id in where]
        open_origin = [nc for nc in originated if state.cards[nc].is_open]
        machine_state = None
        if node.equipment_id and history.machine_events.get(node.equipment_id):
            machine_state = history.machine_events[node.equipment_id][-1].machine_state
        alarm = (
            "alarm"
            if open_here or machine_state in MACHINE_DEVIATIONS
            else ("warning" if open_origin else "ok")
        )
        nodes.append(
            {
                "node_id": node.node_id,
                "title": node.title,
                "kind": node.kind,
                "x": node.x,
                "y": node.y,
                "station_id": node.station_id,
                "equipment_id": node.equipment_id,
                "processing": node.processing,
                "checkpoint_kind": node.checkpoint_kind,
                "assembly": node.assembly,
                "duration_s": node.duration_s,
                "machine_state": machine_state,
                "state": alarm,
                "passed": len(passed),
                "problems": {
                    "nonconformance": active["nonconformance"],
                    "not_assessable": active["not_assessable"],
                    "machine": active["machine"],
                },
                "open_detections": len(open_here),
                "detections": len(detected),
                "originated": len(originated),
                "open_originated": len(open_origin),
                "counts": dict(by_status),
            }
        )
    alarms = sorted(
        (
            {
                "nc_id": nc,
                "node_id": where,
                "item_id": state.cards[nc].item_id,
                "defect_type": state.cards[nc].defect_type,
                "status": state.cards[nc].status,
                "at": state.cards[nc].first_detected_at.isoformat(),
            }
            for nc, where in index.detected_at.items()
            if state.cards[nc].is_open
        ),
        key=lambda alarm: alarm["at"],
        reverse=True,
    )[:12]
    return {
        "line_id": config.line_id,
        "title": config.title,
        "version": config.version,
        "product_type_id": config.product_type_id,
        "item_types": config.item_types(),
        "nodes": nodes,
        "edges": [list(edge) for edge in config.edges],
        "items": items,
        "entry_counts": dict(counts.get("__entry", {})),
        "alarms": alarms,
        "latest_event_at": latest.isoformat() if latest else None,
    }


def timeline(index: LineIndex, state: State) -> dict:
    """
    Границы шкалы времени и отметки на ней, двумя дорожками.

    Дорожка изделий: обнаружение проблемы (несоответствие или «оценка невозможна») и её
    решение — закрытие после устранения, отклонение ложного сигнала, повторная достоверная
    проверка. Изделий без проблем на шкале нет. Дорожка станков: выход станка из режима и
    его возврат в работу. У обеих отметок проблемы общий problem_id — по нему интерфейс
    связывает возникновение и решение.
    """

    events = _line_events(index, state)
    if not events:
        return {"start": None, "end": None, "markers": []}
    markers = []
    lanes = [("items", line_problems(index, state)), ("machines", machine_problems(index, state))]
    for lane, problems in lanes:
        for problem in problems:
            base = {
                "lane": lane,
                "problem_id": problem["problem_id"],
                "problem_kind": problem["kind"],
                "node_id": problem["node_id"],
                "item_id": problem["item_id"],
                "equipment_id": problem.get("equipment_id"),
                "title": problem["title"],
                "note": problem["note"],
            }
            markers.append({**base, "at": problem["at"].isoformat(), "kind": "problem"})
            if problem["resolved_at"]:
                markers.append(
                    {
                        **base,
                        "at": problem["resolved_at"].isoformat(),
                        "kind": "resolved",
                        "resolution": problem["resolution"],
                    }
                )
    markers.sort(key=lambda marker: marker["at"])
    return {
        "start": events[0].occurred_at.isoformat(),
        "end": events[-1].occurred_at.isoformat(),
        "markers": markers[-800:],
    }


def plant_card(index: LineIndex, state: State) -> dict:
    """Карточка линии для обзора производства."""

    config = index.config
    items = [item_id for item_id, line in index.item_line.items() if line == config.line_id]
    statuses = Counter(state.statuses.get(item_id) for item_id in items)
    open_cards = [nc for nc in index.detected_at if state.cards[nc].is_open]
    runs = [run for node_runs in index.runs.values() for run in node_runs]
    products = [
        item_id for item_id in items if index.item_types.get(item_id) == config.product_type_id
    ]
    finals = [
        item_id
        for item_id in products
        if any(
            obs.event.checkpoint_kind == "final"
            for obs in state.history.items[item_id].observations
        )
    ]
    good = [item_id for item_id in finals if state.statuses.get(item_id) == "conforming"]
    events = _line_events(index, state)
    return {
        "line_id": config.line_id,
        "title": config.title,
        "product_type_id": config.product_type_id,
        "item_types": config.item_types(),
        "stages": len(config.nodes),
        "nodes": [
            {
                "node_id": node.node_id,
                "x": node.x,
                "y": node.y,
                "kind": node.kind,
                "title": node.title,
            }
            for node in config.nodes
        ],
        "edges": [list(edge) for edge in config.edges],
        "alarm_nodes": sorted({index.detected_at[nc] for nc in open_cards}),
        "items": len(items),
        "statuses": dict(statuses),
        "in_progress_runs": sum(1 for run in runs if run.status == "in_progress"),
        "open_nonconformances": len(open_cards),
        "final_yield": round(len(good) / len(finals), 3) if finals else None,
        "latest_event_at": events[-1].occurred_at.isoformat() if events else None,
    }


def economics(index: LineIndex, state: State) -> dict:
    """Экономика линии: параметры главной роли, умноженные на измеренные величины."""

    config = index.config
    params = config.economics
    history = state.history
    line_items = [item_id for item_id, line in index.item_line.items() if line == config.line_id]
    products = [
        item_id for item_id in line_items if index.item_types.get(item_id) == config.product_type_id
    ]
    finished = [item_id for item_id in products if state.statuses.get(item_id) == "conforming"]
    nonconforming = [
        item_id
        for item_id in products + line_items
        if state.statuses.get(item_id) == "nonconforming"
    ]
    runs = [run for node_runs in index.runs.values() for run in node_runs]
    rework = sum(1 for run in runs if run.is_rework)
    station_hours = sum((run.station_time_s or 0) for run in runs) / 3600
    medians = {}
    for node in config.nodes:
        if node.kind == "operation":
            values = [
                run.active_time_s
                for run in index.runs.get(node.node_id, [])
                if run.active_time_s is not None and not run.is_rework
            ]
            if values:
                medians[node.node_id] = median(values)
    bottleneck = max(medians, key=medians.get) if medians else None
    capacity = (params.shift_hours * 3600 / medians[bottleneck]) if bottleneck else None
    rework_cost = rework * params.rework_cost_rub
    scrap_cost = len(set(nonconforming)) * params.scrap_cost_rub
    labour_cost = station_hours * params.hour_cost_rub
    output_value = len(finished) * params.item_value_rub
    losses_by_node = {}
    for nc, (nodes, strong) in index.origins.items():
        card = state.cards.get(nc)
        if card is None or card.status not in CONFIRMED_STATUSES or not strong:
            continue
        cost = params.rework_cost_rub if card.status == "closed" else params.scrap_cost_rub
        losses_by_node[nodes[0]] = losses_by_node.get(nodes[0], 0) + cost
    finals = [
        item_id
        for item_id in products
        if any(obs.event.checkpoint_kind == "final" for obs in history.items[item_id].observations)
    ]
    return {
        "line_id": config.line_id,
        "parameters": {
            "item_value_rub": params.item_value_rub,
            "rework_cost_rub": params.rework_cost_rub,
            "scrap_cost_rub": params.scrap_cost_rub,
            "hour_cost_rub": params.hour_cost_rub,
            "shift_hours": params.shift_hours,
            "note": params.note,
        },
        "measured": {
            "products": len(products),
            "finished_conforming": len(finished),
            "with_final_inspection": len(finals),
            "nonconforming_items": len(set(nonconforming)),
            "rework_runs": rework,
            "station_hours": round(station_hours, 2),
            "bottleneck_node": bottleneck,
            "bottleneck_median_s": round(medians[bottleneck], 1) if bottleneck else None,
        },
        "computed": {
            "output_value_rub": round(output_value),
            "rework_cost_rub": round(rework_cost),
            "scrap_cost_rub": round(scrap_cost),
            "labour_cost_rub": round(labour_cost),
            "losses_share": round((rework_cost + scrap_cost) / output_value, 4)
            if output_value
            else None,
            "cost_per_good_item_rub": round(
                (labour_cost + rework_cost + scrap_cost) / len(finished)
            )
            if finished
            else None,
            "capacity_per_shift": round(capacity, 1) if capacity else None,
            "final_yield": round(len(finished) / len(finals), 3) if finals else None,
            "losses_by_node_rub": {
                node: round(value) for node, value in sorted(losses_by_node.items())
            },
        },
        "origin": "параметры задаёт главная роль линии; "
        "количества и длительности измерены по истории",
    }
