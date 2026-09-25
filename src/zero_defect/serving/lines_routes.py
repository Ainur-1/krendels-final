"""
API линий: граф и его живое состояние, статистика по этапам, маршрут изделия,
экономика, управление эмуляцией и создание новых линий.

Основной экран интерфейса строится из этих эндпоинтов: граф линии, этапы с фильтрами
по столбцам и боковая панель с деталями. Изделие здесь — точечный случай, в который
проваливаются из этапа или таблицы.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError

from zero_defect.config import DATA_DIR
from zero_defect.lines.emulator import LiveEmulator
from zero_defect.lines.flow import FlowFile, speed_for
from zero_defect.lines.model import Economics, LineConfig, Node
from zero_defect.lines.store import LineError, LineStore
from zero_defect.lines.views import (
    Filters,
    LineIndex,
    build_index,
    current_stage,
    economics,
    item_path,
    live_view,
    plant_card,
    stage_items,
    stage_stats,
    stage_table,
    timeline,
)
from zero_defect.quality.nonconformance import CONFIRMED_STATUSES
from zero_defect.security.auth import Principal
from zero_defect.service import QualitySystem
from zero_defect.serving import payloads


class EmulationRequest(BaseModel):
    action: str = Field(pattern="^(start|stop)$")
    speed: float | None = None


class InjectRequest(BaseModel):
    node_id: str
    kind: str = Field(default="defect", pattern="^(defect|deviation)$")


class StepSpec(BaseModel):
    """Этап новой линии, как его задаёт главная роль в редакторе."""

    title: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(operation|inspection)$")
    duration_min: float = Field(gt=0, le=600)
    defect_rate_pct: float = Field(default=0, ge=0, le=100)
    defect_types: list[str] = []
    checkpoint_kind: str | None = None
    equipment_id: str | None = None
    operators: list[str] = []
    rework_types: list[str] = []


class LineSpec(BaseModel):
    line_id: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
    title: str = Field(min_length=1, max_length=160)
    product_type_id: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str = ""
    takt_min: float = Field(gt=0, le=600)
    steps: list[StepSpec] = Field(min_length=1, max_length=40)
    economics: dict = {}


class GraphNodeSpec(BaseModel):
    """Блок редактора линии: этап со своими параметрами и положением на холсте."""

    node_id: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")
    title: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(operation|inspection)$")
    x: float = 0
    y: float = 0
    duration_min: float = Field(gt=0, le=600)
    defect_rate_pct: float = Field(default=0, ge=0, le=100)
    defect_types: list[str] = []
    rework_types: list[str] = []
    equipment_id: str | None = None
    operators: list[str] = []
    checkpoint_kind: str | None = Field(default=None, pattern="^(incoming|after_operation|final)$")
    item_type_id: str | None = None
    origin: str = Field(default="manufactured", pattern="^(manufactured|purchased)$")
    output_type: str | None = None


class GraphSpec(BaseModel):
    """Линия, собранная в редакторе из блоков и связей."""

    line_id: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
    title: str = Field(min_length=1, max_length=160)
    product_type_id: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str = ""
    takt_min: float = Field(gt=0, le=600)
    nodes: list[GraphNodeSpec] = Field(min_length=1, max_length=60)
    edges: list[tuple[str, str]] = []
    economics: dict = {}


class RunSpec(BaseModel):
    """Прогон на существующей линии, настроенный в пульте эмулятора."""

    items: int = Field(ge=1, le=200)
    duration_s: float = Field(default=60, ge=10, le=900)
    seed: int = 1
    defect_rates_pct: dict[str, float] = {}
    defects: list[dict] = []


def graph_to_config(spec: GraphSpec) -> LineConfig:
    """Граф из редактора → конфигурация линии. Идентификаторы участков выводятся сами."""

    incoming = {target for _, target in spec.edges}
    outgoing = {source for source, _ in spec.edges}
    nodes = []
    for block in spec.nodes:
        is_source = block.node_id not in incoming
        is_sink = block.node_id not in outgoing
        merges = sum(1 for _, target in spec.edges if target == block.node_id) > 1
        checkpoint_kind = None
        if block.kind == "inspection":
            checkpoint_kind = block.checkpoint_kind or (
                "incoming" if is_source else "final" if is_sink else "after_operation"
            )
        nodes.append(
            Node(
                node_id=block.node_id,
                title=block.title,
                kind=block.kind,
                station_id=f"ST-{block.node_id}",
                x=block.x,
                y=block.y,
                duration_s=block.duration_min * 60,
                operation_id=f"OP-{block.node_id}" if block.kind == "operation" else None,
                equipment_id=(block.equipment_id or f"EQ-{block.node_id}")
                if block.kind == "operation"
                else None,
                operators=block.operators
                or ([f"OP-{block.node_id}"] if block.kind == "operation" else []),
                assembly=block.kind == "operation" and merges,
                output_type=(block.output_type or spec.product_type_id) if merges else None,
                checkpoint_id=f"CP-{block.node_id}" if block.kind == "inspection" else None,
                checkpoint_kind=checkpoint_kind,
                item_type_id=(block.item_type_id or spec.product_type_id) if is_source else None,
                origin=block.origin,
                defect_rate=block.defect_rate_pct / 100,
                defect_types=[code.strip().upper() for code in block.defect_types if code.strip()],
                quality_issue_rate=0.02 if block.kind == "inspection" else 0.0,
                rework_types=[code.strip().upper() for code in block.rework_types if code.strip()],
            )
        )
    allowed = {
        "item_value_rub",
        "rework_cost_rub",
        "scrap_cost_rub",
        "hour_cost_rub",
        "shift_hours",
    }
    params = {key: float(value) for key, value in spec.economics.items() if key in allowed}
    return LineConfig(
        line_id=spec.line_id,
        title=spec.title,
        product_type_id=spec.product_type_id,
        description=spec.description,
        nodes=nodes,
        edges=[tuple(edge) for edge in spec.edges],
        takt_s=spec.takt_min * 60,
        economics=Economics(**params, note="Параметры заданы в редакторе линии."),
    )


def spec_to_config(spec: LineSpec) -> LineConfig:
    """Линейный процесс из редактора → граф: этапы по порядку, связи между соседними."""

    nodes = []
    count = len(spec.steps)
    for index, step in enumerate(spec.steps):
        node_id = f"{spec.line_id}-S{index + 1:02d}"
        station = f"ST-{spec.line_id}-{index + 1:02d}"
        is_first = index == 0
        kind = step.checkpoint_kind or (
            "incoming" if is_first else "final" if index == count - 1 else "after_operation"
        )
        nodes.append(
            Node(
                node_id=node_id,
                title=step.title,
                kind=step.kind,
                station_id=station,
                x=60 + (index * 860 / max(count - 1, 1)),
                y=220,
                duration_s=step.duration_min * 60,
                operation_id=f"OP-{spec.line_id}-{index + 1:02d}0"
                if step.kind == "operation"
                else None,
                equipment_id=(step.equipment_id or f"EQ-{spec.line_id}-{index + 1:02d}")
                if step.kind == "operation"
                else None,
                operators=step.operators
                or ([f"OP-{spec.line_id}-{index + 1}"] if step.kind == "operation" else []),
                checkpoint_id=f"CP-{spec.line_id}-{index + 1:02d}"
                if step.kind == "inspection"
                else None,
                checkpoint_kind=kind if step.kind == "inspection" else None,
                item_type_id=spec.product_type_id if is_first else None,
                defect_rate=step.defect_rate_pct / 100,
                defect_types=step.defect_types,
                quality_issue_rate=0.02 if step.kind == "inspection" else 0.0,
                rework_types=step.rework_types,
            )
        )
    edges = [(nodes[i].node_id, nodes[i + 1].node_id) for i in range(count - 1)]
    allowed = {
        "item_value_rub",
        "rework_cost_rub",
        "scrap_cost_rub",
        "hour_cost_rub",
        "shift_hours",
    }
    params = {key: float(value) for key, value in spec.economics.items() if key in allowed}
    return LineConfig(
        line_id=spec.line_id,
        title=spec.title,
        product_type_id=spec.product_type_id,
        description=spec.description,
        nodes=nodes,
        edges=edges,
        takt_s=spec.takt_min * 60,
        economics=Economics(**params, note="Параметры заданы при создании линии."),
    )


class IndexCache:
    """Раскладка истории по этапам пересчитывается, только когда меняется состояние."""

    def __init__(self, system: QualitySystem, lines: LineStore) -> None:
        self.system = system
        self.lines = lines
        self._lock = threading.Lock()
        self._cache: dict[tuple, LineIndex] = {}

    def get(self, line_id: str, at: str | None = None) -> tuple[LineIndex, object]:
        state = self.system.state_at(_moment(at)) if at else self.system.snapshot()
        config = self.lines.get(line_id)
        if config is None:
            raise HTTPException(404, f"линия {line_id} не найдена")
        key = (id(state), line_id, config.version)
        with self._lock:
            index = self._cache.get(key)
            if index is None:
                if len(self._cache) > 24:
                    self._cache.clear()
                index = self._cache[key] = build_index(config, state)
            return index, state


def _moment(at: str) -> datetime:
    try:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(422, f"неверный момент времени: {at}") from error
    if moment.tzinfo is None:
        raise HTTPException(422, "момент времени должен быть с часовым поясом")
    return moment


def _filters(
    item_type: str | None, since: str | None, until: str | None, shift: str | None
) -> Filters:
    try:
        return Filters(
            item_type=item_type or None,
            since=datetime.fromisoformat(since) if since else None,
            until=datetime.fromisoformat(until) if until else None,
            shift=shift or None,
        )
    except ValueError as error:
        raise HTTPException(422, f"неверный формат даты: {error}") from error


def register(
    app: FastAPI, system: QualitySystem, lines: LineStore, emulator: LiveEmulator, principal, reader
) -> IndexCache:
    cache = IndexCache(system, lines)

    def manager(user: Principal = Depends(principal)) -> Principal:
        return user

    @app.get("/api/lines")
    def list_lines(_: Principal = Depends(reader)) -> list[dict]:
        return [
            {
                "line_id": config.line_id,
                "title": config.title,
                "product_type_id": config.product_type_id,
                "version": config.version,
                "item_types": config.item_types(),
                "takt_s": config.takt_s,
                "stages": len(config.nodes),
                "emulation": emulator.status(config.line_id),
            }
            for config in lines.all()
        ]

    @app.get("/api/lines/{line_id}")
    def get_line(line_id: str, _: Principal = Depends(reader)) -> dict:
        config = lines.get(line_id)
        if config is None:
            raise HTTPException(404, f"линия {line_id} не найдена")
        return {**json.loads(config.to_json()), "order": [node.node_id for node in config.order()]}

    @app.post("/api/lines")
    def create_line(spec: LineSpec, user: Principal = Depends(manager)) -> dict:
        system.security.authorize(user, "line_manage", "line_create", {"line_id": spec.line_id})
        if lines.get(spec.line_id) is not None:
            raise HTTPException(409, f"линия {spec.line_id} уже есть")
        try:
            config = lines.save(spec_to_config(spec), user.user_id)
        except LineError as error:
            raise HTTPException(422, str(error)) from error
        return {"line_id": config.line_id, "version": config.version}

    @app.put("/api/lines/{line_id}/economics")
    def update_economics(line_id: str, params: dict, user: Principal = Depends(manager)) -> dict:
        system.security.authorize(user, "line_manage", "line_economics", {"line_id": line_id})
        config = lines.get(line_id)
        if config is None:
            raise HTTPException(404, f"линия {line_id} не найдена")
        allowed = {
            "item_value_rub",
            "rework_cost_rub",
            "scrap_cost_rub",
            "hour_cost_rub",
            "shift_hours",
        }
        for key, value in params.items():
            if key in allowed:
                setattr(config.economics, key, float(value))
        config.economics.note = f"Параметры изменены пользователем {user.user_id}."
        saved = lines.save(config, user.user_id)
        return {"line_id": saved.line_id, "version": saved.version}

    @app.get("/api/lines/{line_id}/live")
    def line_live(line_id: str, at: str | None = None, _: Principal = Depends(reader)) -> dict:
        index, state = cache.get(line_id, at)
        return {**live_view(index, state), "emulation": emulator.status(line_id)}

    @app.get("/api/lines/{line_id}/stages")
    def stages(
        line_id: str,
        at: str | None = None,
        item_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        shift: str | None = None,
        _: Principal = Depends(reader),
    ) -> list[dict]:
        index, state = cache.get(line_id, at)
        return stage_table(index, state, system.settings, _filters(item_type, since, until, shift))

    @app.get("/api/lines/{line_id}/stages/{node_id}")
    def stage(
        line_id: str,
        node_id: str,
        at: str | None = None,
        item_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        shift: str | None = None,
        _: Principal = Depends(reader),
    ) -> dict:
        index, state = cache.get(line_id, at)
        try:
            node = index.config.node(node_id)
        except KeyError as error:
            raise HTTPException(404, f"этап {node_id} не найден") from error
        filters = _filters(item_type, since, until, shift)
        detected = [
            payloads.nc_summary(state.cards[nc])
            for nc, where in index.detected_at.items()
            if where == node_id
        ]
        originated = [
            payloads.nc_summary(state.cards[nc])
            for nc, (where, _) in index.origins.items()
            if node_id in where
        ]
        return {
            "stats": stage_stats(index, node, state, system.settings, filters),
            "items": stage_items(index, node, state, system.settings, filters),
            "detected": sorted(detected, key=lambda card: card["first_detected_at"], reverse=True)[
                :30
            ],
            "originated": sorted(
                originated, key=lambda card: card["first_detected_at"], reverse=True
            )[:30],
            "config": {
                "duration_s": node.duration_s,
                "defect_rate": node.defect_rate,
                "defect_types": node.defect_types,
                "operators": node.operators,
                "rework_types": node.rework_types,
            },
        }

    @app.get("/api/lines/{line_id}/economics")
    def line_economics(line_id: str, user: Principal = Depends(reader)) -> dict:
        index, state = cache.get(line_id)
        return economics(index, state)

    @app.get("/api/lines/{line_id}/overview")
    def overview(line_id: str, at: str | None = None, _: Principal = Depends(reader)) -> dict:
        """Сводка для панели роли: у каждой роли своё «в первую очередь»."""

        index, state = cache.get(line_id, at)
        history = state.history
        items = [item_id for item_id, line in index.item_line.items() if line == line_id]
        cards = [state.cards[nc] for nc in index.detected_at]
        open_cards = sorted(
            (card for card in cards if card.is_open),
            key=lambda card: (payloads.SEVERITY.get(card.severity, 9), card.first_detected_at),
        )
        runs = [run for node_runs in index.runs.values() for run in node_runs]
        statuses = [state.statuses.get(item_id) for item_id in items]
        origin_counts: dict[str, int] = {}
        for where, _ in index.origins.values():
            for node_id in where:
                origin_counts[node_id] = origin_counts.get(node_id, 0) + 1
        hypotheses: dict[str, int] = {}
        for card in cards:
            if card.assessment and not card.confirmed_cause and card.status != "rejected":
                key = card.assessment["presumed_cause"]
                hypotheses[key] = hypotheses.get(key, 0) + 1
        deviations = []
        for node in index.config.nodes:
            for event in history.machine_events.get(node.equipment_id or "", [])[-50:]:
                if event.machine_state in ("warning", "deviation", "stopped"):
                    deviations.append(
                        {
                            "node_id": node.node_id,
                            "equipment_id": event.equipment_id,
                            "state": event.machine_state,
                            "message": event.message,
                            "at": event.occurred_at.isoformat(),
                        }
                    )
        late = [
            event
            for event in history.events
            if "late" in event.flags
            and (event.line_id or index.item_line.get(event.item_id)) == line_id
        ]
        return {
            "kpi": {
                "items": len(items),
                "conforming": statuses.count("conforming"),
                "nonconforming": statuses.count("nonconforming"),
                "suspect": statuses.count("suspect"),
                "not_assessable": statuses.count("not_assessable"),
                "in_progress_runs": sum(1 for run in runs if run.status == "in_progress"),
                "open_nonconformances": len(open_cards),
                "confirmed": sum(1 for card in cards if card.status in CONFIRMED_STATUSES),
                "rework_runs": sum(1 for run in runs if run.is_rework),
                "late_events": len(late),
            },
            "queue": [payloads.nc_summary(card) for card in open_cards[:25]],
            "in_progress": [
                {
                    "run_id": run.run_id,
                    "item_id": run.item_id,
                    "node_id": index.run_node[run.run_id],
                    "operator_id": run.operator_id,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                }
                for run in runs
                if run.status == "in_progress"
            ][:25],
            "deviations": sorted(deviations, key=lambda entry: entry["at"], reverse=True)[:10],
            "origins_by_node": dict(sorted(origin_counts.items(), key=lambda pair: -pair[1])),
            "hypotheses": hypotheses,
            "source_gaps": {
                source: gaps
                for source, gaps in history.source_gaps().items()
                if line_id.lower() in source.lower()
            },
        }

    @app.get("/api/lines/{line_id}/items")
    def line_items(
        line_id: str,
        at: str | None = None,
        item_type: str | None = None,
        status: str | None = None,
        stage: str | None = None,
        q: str | None = None,
        limit: int = 200,
        _: Principal = Depends(reader),
    ) -> dict:
        """Изделия линии с фильтрами по столбцам: тип, статус, текущий этап, поиск по номеру."""

        index, state = cache.get(line_id, at)
        history = state.history
        rows = []
        for item_id, line in index.item_line.items():
            if line != line_id:
                continue
            item = history.items[item_id]
            row = {
                "item_id": item_id,
                "item_type_id": item.item_type_id,
                "status": state.statuses.get(item_id),
                "stage": current_stage(index, item_id, state),
                "parent_id": item.parent_id,
                "work_order_id": item.work_order_id,
                "open_nc": sum(
                    1
                    for card in state.cards.values()
                    if card.item_id in {item_id, *item.components} and card.is_open
                ),
                "last_at": max(
                    (history.events_by_id[e].occurred_at for e in item.event_ids), default=None
                ),
            }
            if item_type and row["item_type_id"] != item_type:
                continue
            if status and row["status"] != status:
                continue
            if stage and row["stage"] != stage:
                continue
            if q and q.lower() not in item_id.lower():
                continue
            rows.append(row)
        rows.sort(
            key=lambda row: row["last_at"].timestamp() if row["last_at"] else 0.0, reverse=True
        )
        total = len(rows)
        for row in rows:
            row["last_at"] = row["last_at"].isoformat() if row["last_at"] else None
        titles = {node.node_id: node.title for node in index.config.nodes}
        return {
            "total": total,
            "rows": rows[: max(1, min(limit, 1000))],
            "stage_titles": titles,
            "facets": {
                "item_type": index.config.item_types(),
                "status": sorted({s for s in state.statuses.values() if s}),
                "stage": [node.node_id for node in index.config.order()],
            },
        }

    @app.get("/api/items/{item_id}/path")
    def path(item_id: str, at: str | None = None, _: Principal = Depends(reader)) -> dict:
        state = system.state_at(_moment(at)) if at else system.snapshot()
        item = state.history.items.get(item_id)
        if item is None:
            raise HTTPException(404, f"изделие {item_id} не найдено")
        line_id = item.line_id
        parent = state.history.items.get(item.parent_id) if item.parent_id else None
        line_id = line_id or (parent.line_id if parent else None)
        if not line_id or lines.get(line_id) is None:
            raise HTTPException(404, "изделие не относится ни к одной линии")
        index, state = cache.get(line_id, at)
        return item_path(index, item_id, state)

    @app.post("/api/lines/{line_id}/emulation")
    def emulation(
        line_id: str, request: EmulationRequest, user: Principal = Depends(manager)
    ) -> dict:
        system.security.authorize(
            user, "emulate", f"emulation_{request.action}", {"line_id": line_id}
        )
        if lines.get(line_id) is None:
            raise HTTPException(404, f"линия {line_id} не найдена")
        if request.action == "start":
            return emulator.start(line_id, request.speed)
        return emulator.stop(line_id)

    @app.get("/api/flows/example")
    def flow_example(_: Principal = Depends(reader)) -> dict:
        """Пример потока на минуту показа — он же шаблон для своего файла."""

        return json.loads((DATA_DIR / "flows" / "bracket_minute.json").read_text(encoding="utf-8"))

    @app.post("/api/flows")
    def upload_flow(flow: FlowFile, user: Principal = Depends(manager)) -> dict:
        """Загруженный поток: линия создаётся или обновляется, и прогон сразу идёт на экране."""

        system.security.authorize(
            user,
            "line_manage",
            "flow_upload",
            {"line_id": flow.line.get("line_id"), "items": flow.run.items},
        )
        try:
            spec = LineSpec(**flow.line)
        except ValidationError as error:
            raise HTTPException(422, f"линия в файле некорректна: {error.errors()[:3]}") from error
        try:
            config = lines.save(spec_to_config(spec), user.user_id)
        except LineError as error:
            raise HTTPException(422, str(error)) from error
        forced: dict[int, dict[str, str]] = {}
        for defect in flow.run.defects:
            node = config.nodes[defect.stage - 1]
            forced.setdefault(defect.item, {})[node.node_id] = defect.kind
        speed = speed_for(config, flow.run.items, flow.run.duration_s)
        status = emulator.start_run(
            config, flow.run.items, speed, flow.run.duration_s, forced, flow.run.seed
        )
        return {
            "line_id": config.line_id,
            "version": config.version,
            "speed": round(speed, 1),
            "emulation": status,
        }

    @app.post("/api/lines/{line_id}/emulation/inject")
    def inject(line_id: str, request: InjectRequest, user: Principal = Depends(manager)) -> dict:
        system.security.authorize(
            user,
            "emulate",
            "emulation_inject",
            {"line_id": line_id, "node_id": request.node_id, "kind": request.kind},
        )
        config = lines.get(line_id)
        if config is None:
            raise HTTPException(404, f"линия {line_id} не найдена")
        try:
            config.node(request.node_id)
        except KeyError as error:
            raise HTTPException(404, f"этап {request.node_id} не найден") from error
        return emulator.inject(line_id, request.node_id, request.kind)

    @app.get("/api/plant")
    def plant(at: str | None = None, _: Principal = Depends(reader)) -> dict:
        """Обзор производства: все линии и изделия, которые они выпускают."""

        cards = []
        for config in lines.all():
            index, state = cache.get(config.line_id, at)
            cards.append({**plant_card(index, state), "emulation": emulator.status(config.line_id)})
        products: dict[str, list[str]] = {}
        for config in lines.all():
            for item_type in config.item_types():
                products.setdefault(item_type, []).append(config.line_id)
        return {
            "lines": cards,
            "products": [
                {"item_type": key, "lines": value} for key, value in sorted(products.items())
            ],
        }

    @app.get("/api/lines/{line_id}/timeline")
    def line_timeline(line_id: str, _: Principal = Depends(reader)) -> dict:
        index, state = cache.get(line_id)
        return timeline(index, state)

    @app.post("/api/lines/graph")
    def create_graph(spec: GraphSpec, user: Principal = Depends(manager)) -> dict:
        system.security.authorize(user, "line_manage", "line_create", {"line_id": spec.line_id})
        if lines.get(spec.line_id) is not None:
            raise HTTPException(409, f"линия {spec.line_id} уже есть")
        try:
            config = lines.save(graph_to_config(spec), user.user_id)
        except LineError as error:
            raise HTTPException(422, str(error)) from error
        return {"line_id": config.line_id, "version": config.version}

    @app.put("/api/lines/{line_id}/graph")
    def update_graph(line_id: str, spec: GraphSpec, user: Principal = Depends(manager)) -> dict:
        system.security.authorize(user, "line_manage", "line_update", {"line_id": line_id})
        if spec.line_id != line_id or lines.get(line_id) is None:
            raise HTTPException(404, f"линия {line_id} не найдена")
        try:
            config = lines.save(graph_to_config(spec), user.user_id)
        except LineError as error:
            raise HTTPException(422, str(error)) from error
        return {"line_id": config.line_id, "version": config.version}

    @app.post("/api/lines/{line_id}/runs")
    def start_run(line_id: str, spec: RunSpec, user: Principal = Depends(manager)) -> dict:
        """Прогон на существующей линии: данные контракта вбиваются в пульте эмулятора."""

        system.security.authorize(
            user, "emulate", "run_start", {"line_id": line_id, "items": spec.items}
        )
        config = lines.get(line_id)
        if config is None:
            raise HTTPException(404, f"линия {line_id} не найдена")
        run_config = LineConfig.from_dict(json.loads(config.to_json()))
        run_config.version = config.version
        for node_id, rate in spec.defect_rates_pct.items():
            try:
                run_config.node(node_id).defect_rate = max(0.0, min(float(rate), 100.0)) / 100
            except KeyError as error:
                raise HTTPException(422, f"этап {node_id} не найден") from error
        forced: dict[int, dict[str, str]] = {}
        for entry in spec.defects:
            node_id, item, kind = (
                entry.get("node_id"),
                int(entry.get("item", 0)),
                entry.get("kind", "defect"),
            )
            if (
                node_id not in {node.node_id for node in run_config.nodes}
                or not 1 <= item <= spec.items
            ):
                raise HTTPException(422, f"дефект указан вне линии или прогона: {entry}")
            if kind not in ("defect", "deviation"):
                raise HTTPException(422, f"вид {kind!r} неизвестен")
            forced.setdefault(item, {})[node_id] = kind
        speed = speed_for(run_config, spec.items, spec.duration_s)
        status = emulator.start_run(
            run_config, spec.items, speed, spec.duration_s, forced, spec.seed
        )
        return {"line_id": line_id, "speed": round(speed, 1), "emulation": status}

    return cache
