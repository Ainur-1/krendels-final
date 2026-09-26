"""
Решение человека двигает изделие в эмуляции линии.

Эмулятор изолирует изделие, у которого обнаружен дефект: дальше по линии оно не идёт и
ждёт решения. Здесь решение превращается в следующие события линии, как это было бы на
участке:
- подтверждение дефекта, возникшего на операции, — доработка на этой операции, повторный
  контроль в той же точке и путь дальше по линии;
- отклонение сигнала или закрытие несоответствия — изделие идёт дальше без доработки;
- запрос повторного контроля — ещё один снимок в той же точке: признак подтвердится или
  нет;
- повторный контроль по «оценка невозможна» — достоверный снимок, который её снимает.

Решение, которое изделие не держит (оно уже прошло дальше), событий не порождает. Ядро об
эмуляции не знает: оно только сообщает слушателям о записанном решении.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from zero_defect.lines.emulator import LiveEmulator, short_type
from zero_defect.lines.model import LineConfig, Node
from zero_defect.lines.views import LineIndex, current_stage, item_line

# Решение старше двух минут — это история демонстрационной базы, а не действие человека
# сейчас: её проигрывание при запуске не должно двигать изделия.
LIVE_WINDOW_S = 120
# Доля повторных проверок, которые снова находят признак: без неё повторный контроль
# всегда снимал бы сигнал, и решение «запросить повторный контроль» ничего бы не решало.
RECHECK_CONFIRMS = 0.6


def follow_decision(payload: dict, system, lines, cache, emulator: LiveEmulator) -> int:
    """Планирует события после решения. Возвращает число запланированных событий."""

    decision = payload["decision"]
    if abs((datetime.now(UTC) - decision.decided_at).total_seconds()) > LIVE_WINDOW_S:
        return 0
    item_id = payload["item_id"]
    state = system.state()
    line_id = item_line(item_id, state.history)
    config = lines.get(line_id) if line_id else None
    if config is None:
        return 0
    index, state = cache.get(line_id)
    item_type = index.item_types.get(item_id) or config.product_type_id
    if "event_id" in payload:
        return _unassessable(payload, decision, config, state, item_id, item_type, emulator)
    card = state.cards.get(decision.nc_id)
    detected = index.detected_at.get(decision.nc_id)
    if card is None or detected is None or current_stage(index, item_id, state) != detected:
        return 0
    checkpoint = config.node(detected)
    if decision.action == "request_recheck":
        defects = None
        if random.random() < RECHECK_CONFIRMS:
            defects = [
                {
                    "defect_type": card.defect_type,
                    "area": card.area or "surface",
                    "severity": card.severity or "major",
                    "description": f"признак {card.defect_type}, повторный контроль",
                }
            ]

        def plan(planner, at):
            planner.recheck(checkpoint, item_id, item_type, at, defects)

        return emulator.follow_up(line_id, plan)
    if decision.action == "confirm":
        rework = _rework_node(index, config, decision.nc_id)
        if rework is None:
            # Дефект пришёл со входа: доработать его на линии нельзя, изделие ждёт
            # решения о допуске или списании.
            return 0
        previous = _last_run(index, rework, item_id)
    elif decision.action in {"reject", "close"}:
        rework, previous = None, None
    else:
        return 0
    ready = _ready_components(index, config, state, item_id, checkpoint)
    return emulator.follow_up(
        line_id,
        lambda planner, at: planner.resume(
            item_id, item_type, at, checkpoint, rework, previous, ready
        ),
    )


def _unassessable(payload, decision, config, state, item_id, item_type, emulator) -> int:
    if decision.action != "request_recheck":
        return 0
    event_id = payload["event_id"]
    observation = next(
        (obs for obs in state.history.observations if obs.event.event_id == event_id), None
    )
    if observation is None:
        return 0
    node = config.inspection_node(observation.event.checkpoint_id, item_type)
    if node is None:
        return 0
    return emulator.follow_up(
        config.line_id, lambda planner, at: planner.recheck(node, item_id, item_type, at)
    )


def _rework_node(index: LineIndex, config: LineConfig, nc_id: str) -> Node | None:
    """Операция, на которой дефект возник по разбору системы: последняя из кандидатов."""

    nodes, _ = index.origins.get(nc_id, ([], False))
    operations = [config.node(node) for node in nodes if config.node(node).kind == "operation"]
    return operations[-1] if operations else None


def _last_run(index: LineIndex, node: Node, item_id: str) -> str | None:
    runs = [run for run in index.runs.get(node.node_id, []) if run.item_id == item_id]
    return runs[-1].run_id if runs else None


def _ready_components(
    index: LineIndex, config: LineConfig, state, item_id: str, checkpoint: Node
) -> list[str] | None:
    """
    Компоненты того же комплекта, которые уже ждут у сборки. None — сборки впереди нет
    или комплект ещё не собрался целиком: тогда компонент дойдёт до сборки и остановится.
    """

    merge = next(
        (
            node
            for node in config.chain_from(checkpoint.node_id)[1:]
            if len(config.predecessors(node.node_id)) > 1
        ),
        None,
    )
    if merge is None:
        return None
    prefix = f"{config.line_id}-"
    set_index = item_id.removeprefix(prefix).rsplit("-", 1)[0]
    product_type = merge.output_type or config.product_type_id
    if f"{prefix}{set_index}-{short_type(product_type)}" in state.history.items:
        return None
    ready = []
    for source in config.sources():
        sibling = f"{prefix}{set_index}-{short_type(source.item_type_id or product_type)}"
        if sibling == item_id:
            continue
        chain = []
        for node in config.chain_from(source.node_id):
            if node.node_id == merge.node_id:
                break
            chain.append(node)
        waiting = (
            sibling in state.history.items
            and chain
            and current_stage(index, sibling, state) == chain[-1].node_id
            and state.statuses.get(sibling) not in {"suspect", "nonconforming"}
        )
        if not waiting:
            return None
        ready.append(sibling)
    return ready
