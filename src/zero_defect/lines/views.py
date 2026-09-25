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
from zero_defect.lines.model import LineConfig, Node
from zero_defect.quality.nonconformance import CONFIRMED_STATUSES
from zero_defect.service import State

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


def _item_line(item_id: str, history) -> str | None:
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
    item_line = {item_id: _item_line(item_id, history) for item_id in history.items}
    index = LineIndex(config, item_types, item_line)
    for run in history.runs.values():
        line = run.line_id or item_line.get(run.item_id)
        if line != config.line_id:
            continue
        node = config.operation_node(run.station_id, run.operation_id)
        if node is not None:
            index.runs[node.node_id].append(run)
            index.run_node[run.run_id] = node.node_id
    for observation in history.observations:
        event = observation.event
        if (event.line_id or item_line.get(event.item_id)) != config.line_id:
            continue
        node = config.inspection_node(event.checkpoint_id, item_types.get(event.item_id))
        if node is not None:
            index.observations[node.node_id].append(observation)
    for card in state.cards.values():
        event = card.first_signal.observation.event
        if (event.line_id or item_line.get(card.item_id)) != config.line_id:
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
    }


def live_view(index: LineIndex, state: State, window_s: float = 1800) -> dict:
    """Состояние графа линии: этапы с тревогами и изделия в работе."""

    config = index.config
    history = state.history
    line_events = [
        event
        for event in history.events
        if (event.line_id or index.item_line.get(event.item_id)) == config.line_id
    ]
    latest = max((event.occurred_at for event in line_events), default=None)
    items = []
    if latest is not None:
        recent: dict[str, datetime] = {}
        for event in line_events:
            if event.item_id and (latest - event.occurred_at).total_seconds() <= window_s:
                recent[event.item_id] = max(
                    recent.get(event.item_id, event.occurred_at), event.occurred_at
                )
        for item_id, moment in sorted(recent.items(), key=lambda pair: pair[1], reverse=True)[:40]:
            item = history.items.get(item_id)
            if item is None or (item.parent_id and item.parent_id in recent):
                continue
            node = current_stage(index, item_id, state)
            processing = any(
                history.runs[run_id].status == "in_progress" for run_id in item.run_ids
            )
            items.append(
                {
                    "item_id": item_id,
                    "item_type": item.item_type_id,
                    "node_id": node,
                    "status": state.statuses.get(item_id),
                    "processing": processing,
                    "last_at": moment.isoformat(),
                }
            )
    nodes = []
    for node in config.nodes:
        detected = [nc for nc, where in index.detected_at.items() if where == node.node_id]
        open_here = [nc for nc in detected if state.cards[nc].is_open]
        originated = [nc for nc, (where, _) in index.origins.items() if node.node_id in where]
        machine_state = None
        if node.equipment_id and history.machine_events.get(node.equipment_id):
            machine_state = history.machine_events[node.equipment_id][-1].machine_state
        alarm = (
            "alarm"
            if open_here or machine_state in MACHINE_DEVIATIONS
            else ("warning" if any(state.cards[nc].is_open for nc in originated) else "ok")
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
                "checkpoint_kind": node.checkpoint_kind,
                "assembly": node.assembly,
                "machine_state": machine_state,
                "state": alarm,
                "passed": len(index.runs.get(node.node_id, []))
                + len(index.observations.get(node.node_id, [])),
                "open_detections": len(open_here),
                "detections": len(detected),
                "originated": len(originated),
                "in_work": sum(
                    1 for entry in items if entry["node_id"] == node.node_id and entry["processing"]
                ),
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
        "nodes": nodes,
        "edges": [list(edge) for edge in config.edges],
        "items": items,
        "alarms": alarms,
        "latest_event_at": latest.isoformat() if latest else None,
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
