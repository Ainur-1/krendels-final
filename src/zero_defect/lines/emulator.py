"""
Эмулятор конвейера: ведёт изделия по графу линии и порождает события, как настоящие
источники — терминалы участков, журналы станков, анализаторы контрольных точек.

Он работает в двух режимах. История: заранее планирует сотни изделий за прошедшие смены
вместе с решениями контролёров — так демонстрационная база наполняется воспроизводимо
из зерна, без тяжёлых файлов в репозитории. Живой режим: запускает новое изделие раз в
такт и отдаёт события в систему в реальном времени с ускорением, а дефект можно
принудительно внести в выбранный этап.

Эмулятор ничего не пишет в систему мимо приёма: всё, что он делает, проходит ту же
проверку, журнал и разбор, что и события настоящей линии.
"""

from __future__ import annotations

import heapq
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from zero_defect.lines.equipment import machine_parameters
from zero_defect.lines.model import LineConfig, Node
from zero_defect.quality.nonconformance import nc_id_for

AREAS = ["зона А", "зона Б", "кромка", "отверстие 1", "шов 1", "шов 2", "торец", "плоскость"]
ANALYZER = "visionqc-2.3.1"


def short_type(item_type_id: str) -> str:
    return item_type_id.split("-")[-1]


@dataclass
class Carried:
    """Дефект, который изделие несёт дальше по линии."""

    defect_type: str
    area: str
    origin: str
    run_id: str | None
    item_id: str
    deviation: bool = False
    severity: str = "major"


@dataclass
class Planner:
    """Планирует комплект изделий по графу линии; всё случайное — из одного зерна."""

    config: LineConfig
    rng: random.Random
    prefix: str
    speed: float = 1.0
    # Живая линия задерживает изделие с «оценка невозможна» до решения мастера или
    # контролёра. История демонстрационной базы решений людей по ним не содержит, поэтому
    # там ОТК переснимает изделие в той же точке сам и изделие идёт дальше.
    hold_unclear: bool = False
    sequences: dict[str, int] = field(default_factory=dict)
    events: list[tuple[datetime, dict]] = field(default_factory=list)
    detections: list[dict] = field(default_factory=list)
    counter: int = 0
    runs: int = 0

    # --- события --------------------------------------------------------------------

    def _emit(self, at: datetime, event_type: str, source: str, **fields) -> dict:
        self.counter += 1
        self.sequences[source] = self.sequences.get(source, 0) + 1
        message = {
            "event_id": f"{self.config.line_id}-{self.prefix}-{self.counter:06d}",
            "event_type": event_type,
            "schema_version": "1.0",
            "occurred_at": at.isoformat(),
            "source_id": source,
            "sequence_no": self.sequences[source],
        }
        message.update({key: value for key, value in fields.items() if value is not None})
        self.events.append((at, message))
        return message

    def _src(self, node: Node, role: str) -> str:
        return f"{role}-{self.config.line_id}-{node.node_id}".lower()

    def _dur(self, seconds: float) -> timedelta:
        return timedelta(seconds=seconds / self.speed)

    # --- этапы ------------------------------------------------------------------------

    def _operation(
        self,
        node: Node,
        item_id: str,
        at: datetime,
        carried: list[Carried],
        force: str | None,
        previous_run: str | None = None,
        reason: str | None = None,
    ) -> tuple[datetime, str]:
        self.runs += 1
        run_id = f"R-{self.config.line_id}-{self.prefix}-{self.runs:05d}"
        operator = self.rng.choice(node.operators) if node.operators else None
        term = self._src(node, "term")
        self._emit(
            at,
            "operation_started",
            term,
            item_id=item_id,
            operation_run_id=run_id,
            operation_id=node.operation_id,
            line_id=self.config.line_id,
            station_id=node.station_id,
            operator_id=operator,
            equipment_id=node.equipment_id,
            previous_run_id=previous_run,
            rework_reason=reason,
            identification="reliable",
        )
        machine = self._src(node, "mlog") if node.equipment_id else None
        if machine:
            self._emit(
                at,
                "machine_state",
                machine,
                equipment_id=node.equipment_id,
                line_id=self.config.line_id,
                machine_state="running",
                parameters=machine_parameters(node.machine_type, run_id, deviation=False),
            )
        duration = node.duration_s * (0.35 if previous_run else self.rng.uniform(0.85, 1.2))
        deviation = previous_run is None and (force == "deviation" or self.rng.random() < 0.03)
        if deviation and machine:
            self._emit(
                at + self._dur(duration * 0.4),
                "machine_state",
                machine,
                equipment_id=node.equipment_id,
                line_id=self.config.line_id,
                machine_state="deviation",
                message="параметр режима вне допуска",
                parameters={
                    **machine_parameters(node.machine_type, run_id, deviation=True),
                    "deviation_pct": round(self.rng.uniform(8, 25), 1),
                },
            )
            self._emit(
                at + self._dur(duration * 0.6),
                "machine_state",
                machine,
                equipment_id=node.equipment_id,
                line_id=self.config.line_id,
                machine_state="running",
                parameters=machine_parameters(node.machine_type, f"{run_id}/2", deviation=False),
            )
        paused = 0.0
        if previous_run is None and self.rng.random() < 0.05:
            paused = self.rng.uniform(120, 480)
            self._emit(
                at + self._dur(duration * 0.5),
                "operation_paused",
                term,
                item_id=item_id,
                operation_run_id=run_id,
                pause_reason="смена инструмента",
            )
            self._emit(
                at + self._dur(duration * 0.5 + paused),
                "operation_resumed",
                term,
                item_id=item_id,
                operation_run_id=run_id,
            )
        probability = node.defect_rate * (5 if deviation else 1)
        if (
            previous_run is None
            and node.defect_types
            and (force == "defect" or force == "deviation" or self.rng.random() < probability)
        ):
            carried.append(
                Carried(
                    self.rng.choice(node.defect_types),
                    self.rng.choice(AREAS),
                    node.node_id,
                    run_id,
                    item_id,
                    deviation,
                    self.rng.choice(["minor", "major", "major", "critical"]),
                )
            )
        end = at + self._dur(duration + paused)
        self._emit(
            end,
            "operation_finished",
            term,
            item_id=item_id,
            operation_run_id=run_id,
            outcome="completed",
            # Источник сообщает длительность в масштабе процесса. В живом показе время
            # событий сжато ускорением, а эта длительность — нет: статистика этапа остаётся
            # в минутах техпроцесса, а не в секундах показа.
            reported_duration={
                "value": round(duration, 1),
                "unit": "s",
                "meaning": "active_processing",
            },
        )
        return end + self._dur(self.rng.uniform(30, 150)), run_id

    def _inspection(
        self,
        node: Node,
        item_id: str,
        item_type: str,
        at: datetime,
        carried: list[Carried],
        force: str | None,
    ) -> tuple[datetime, str]:
        """Возвращает время и итог: clean, found или unreliable."""

        if (
            node.checkpoint_kind == "incoming"
            and node.defect_types
            and (force == "defect" or self.rng.random() < node.defect_rate)
        ):
            carried.append(
                Carried(
                    self.rng.choice(node.defect_types),
                    self.rng.choice(AREAS),
                    node.node_id,
                    None,
                    item_id,
                )
            )
        source = self._src(node, "vision")
        common = dict(
            item_id=item_id,
            item_type_id=item_type,
            line_id=self.config.line_id,
            station_id=node.station_id,
            checkpoint_id=node.checkpoint_id,
            checkpoint_kind=node.checkpoint_kind,
            analyzer_version=ANALYZER,
        )
        end = at + self._dur(node.duration_s)
        if self.rng.random() < node.quality_issue_rate:
            if self.rng.random() < 0.5:
                self._emit(
                    at,
                    "inspection_reported",
                    source,
                    inspection_result="not_assessable",
                    observation_quality="poor",
                    confidence=0.2,
                    **common,
                )
            else:
                self._emit(
                    at,
                    "inspection_reported",
                    source,
                    inspection_result="no_defect_signs",
                    observation_quality="degraded",
                    confidence=round(self.rng.uniform(0.35, 0.55), 2),
                    **common,
                )
            return end, "unreliable"
        if carried:
            confidence = round(self.rng.uniform(0.55, 0.97), 2)
            defects = []
            for defect in carried:
                entry = {
                    "defect_type": defect.defect_type,
                    "area": defect.area,
                    "severity": defect.severity,
                    "description": f"признак {defect.defect_type}",
                }
                if defect.item_id != item_id:
                    entry["component_item_id"] = defect.item_id
                defects.append(entry)
            self._emit(
                at,
                "inspection_reported",
                source,
                inspection_result="defect_signs_found",
                defects=defects,
                observation_quality="good",
                confidence=confidence,
                **common,
            )
            for defect in carried:
                self.detections.append(
                    {"at": at, "defect": defect, "confidence": confidence, "node": node.node_id}
                )
            return end, "found"
        self._emit(
            at,
            "inspection_reported",
            source,
            inspection_result="no_defect_signs",
            observation_quality="good",
            confidence=round(self.rng.uniform(0.85, 0.99), 2),
            **common,
        )
        return end, "clean"

    # --- маршрут -----------------------------------------------------------------------

    def _chain_until_merge(self, source: Node) -> tuple[list[Node], Node | None]:
        nodes, merge = [], None
        for node in self.config.chain_from(source.node_id):
            if node.node_id != source.node_id and len(self.config.predecessors(node.node_id)) > 1:
                merge = node
                break
            nodes.append(node)
        return nodes, merge

    def _run(
        self,
        nodes: list[Node],
        item_id: str,
        item_type: str,
        at: datetime,
        carried: list[Carried],
        forced: dict[str, str],
    ) -> tuple[datetime, bool]:
        """Проводит изделие по этапам. Возвращает время окончания и признак изоляции."""

        last_runs: dict[str, str] = {}
        for node in nodes:
            force = forced.pop(node.node_id, None)
            if node.kind == "operation":
                at, run_id = self._operation(node, item_id, at, carried, force)
                last_runs[node.node_id] = run_id
                continue
            at, outcome = self._inspection(node, item_id, item_type, at, carried, force)
            if outcome == "unreliable":
                # Плохой снимок годностью не становится: без повторного контроля в той же
                # точке изделие дальше не идёт.
                if self.hold_unclear:
                    return at, True
                at, outcome = self._reinspect(
                    node, item_id, item_type, at + self._dur(self.rng.uniform(300, 900)), carried
                )
            if outcome != "found":
                continue
            reworkable = [
                defect
                for defect in carried
                if defect.origin in last_runs
                and defect.defect_type in self.config.node(defect.origin).rework_types
            ]
            if reworkable and len(reworkable) == len(carried) and node.checkpoint_kind != "final":
                for defect in reworkable:
                    origin = self.config.node(defect.origin)
                    at, _ = self._operation(
                        origin,
                        item_id,
                        at + self._dur(300),
                        [],
                        None,
                        previous_run=last_runs[defect.origin],
                        reason=f"доработка: {defect.defect_type}",
                    )
                    defect.origin = f"reworked:{defect.origin}"
                carried.clear()
                at, _ = self._inspection(node, item_id, item_type, at, carried, None)
                continue
            # Обнаруженное несоответствие изолируется: изделие дальше по линии не идёт.
            return at, True
        return at, False

    def plan_set(self, start: datetime, index: str, forced: dict[str, str] | None = None) -> None:
        """Один комплект: изделие на каждом входе, сборка, если она есть, и путь до конца."""

        forced = dict(forced or {})
        arrivals: list[tuple[datetime, bool, str, list[Carried]]] = []
        merge: Node | None = None
        order_id = f"WO-{self.config.line_id}-{index}"
        for offset, source in enumerate(self.config.sources()):
            item_type = source.item_type_id or self.config.product_type_id
            item_id = f"{self.config.line_id}-{index}-{short_type(item_type)}"
            at = start + self._dur(offset * 40)
            self._emit(
                at,
                "item_registered",
                f"mes-{self.config.line_id}".lower(),
                item_id=item_id,
                item_type_id=item_type,
                line_id=self.config.line_id,
                work_order_id=order_id,
                origin=source.origin,
            )
            nodes, merge = self._chain_until_merge(source)
            carried: list[Carried] = []
            end, held = self._run(nodes, item_id, item_type, at + self._dur(60), carried, forced)
            arrivals.append((end, held, item_id, carried))
        if merge is None or any(held for _, held, _, _ in arrivals):
            return
        at = max(end for end, _, _, _ in arrivals)
        carried = [defect for *_, component in arrivals for defect in component]
        components = [component for _, _, component, _ in arrivals]
        self._assemble(merge, index, components, at, carried, forced)

    def _assemble(
        self,
        merge: Node,
        index: str,
        components: list[str],
        at: datetime,
        carried: list[Carried],
        forced: dict[str, str],
    ) -> None:
        """Сборка комплекта на узле слияния и путь собранного изделия до конца линии."""

        product_type = merge.output_type or self.config.product_type_id
        product = f"{self.config.line_id}-{index}-{short_type(product_type)}"
        self._emit(
            at,
            "item_registered",
            f"mes-{self.config.line_id}".lower(),
            item_id=product,
            item_type_id=product_type,
            line_id=self.config.line_id,
            work_order_id=f"WO-{self.config.line_id}-{index}",
            origin="manufactured",
        )
        force = forced.pop(merge.node_id, None)
        at, run_id = self._operation(merge, product, at + self._dur(30), carried, force)
        for component in components:
            self._emit(
                at - self._dur(merge.duration_s * 0.5),
                "component_linked",
                self._src(merge, "term"),
                item_id=product,
                component_item_id=component,
                operation_run_id=run_id,
                station_id=merge.station_id,
            )
        downstream = self.config.chain_from(merge.node_id)[1:]
        self._run(downstream, product, product_type, at, carried, forced)

    # --- продолжение после решения человека ---------------------------------------------

    def recheck(
        self,
        node: Node,
        item_id: str,
        item_type: str,
        at: datetime,
        defects: list[dict] | None = None,
    ) -> datetime:
        """Повторный контроль по решению человека: хороший снимок, признак есть или нет."""

        common = dict(
            item_id=item_id,
            item_type_id=item_type,
            line_id=self.config.line_id,
            station_id=node.station_id,
            checkpoint_id=node.checkpoint_id,
            checkpoint_kind=node.checkpoint_kind,
            analyzer_version=ANALYZER,
            observation_quality="good",
        )
        source = self._src(node, "vision")
        if defects:
            self._emit(
                at,
                "inspection_reported",
                source,
                inspection_result="defect_signs_found",
                defects=defects,
                confidence=round(self.rng.uniform(0.8, 0.95), 2),
                **common,
            )
        else:
            self._emit(
                at,
                "inspection_reported",
                source,
                inspection_result="no_defect_signs",
                confidence=round(self.rng.uniform(0.88, 0.99), 2),
                **common,
            )
        return at + self._dur(node.duration_s)

    def _reinspect(
        self, node: Node, item_id: str, item_type: str, at: datetime, carried: list[Carried]
    ) -> tuple[datetime, str]:
        """Повторный достоверный контроль после плохого снимка: признак виден, если он есть."""

        defects = [
            {
                "defect_type": defect.defect_type,
                "area": defect.area,
                "severity": defect.severity,
                "description": f"признак {defect.defect_type}",
                **({"component_item_id": defect.item_id} if defect.item_id != item_id else {}),
            }
            for defect in carried
        ]
        end = self.recheck(node, item_id, item_type, at, defects or None)
        if not carried:
            return end, "clean"
        for defect in carried:
            self.detections.append(
                {"at": at, "defect": defect, "confidence": 0.9, "node": node.node_id}
            )
        return end, "found"

    def resume(
        self,
        item_id: str,
        item_type: str,
        at: datetime,
        checkpoint: Node,
        rework: Node | None = None,
        previous_run: str | None = None,
        ready_components: list[str] | None = None,
    ) -> None:
        """
        Изделие, которое ждало решения у контрольной точки, идёт дальше по линии.

        rework — операция, которую повторяют по решению (у подтверждённого дефекта
        операции); после неё изделие снова проходит ту же контрольную точку. Дойдя до
        сборки, компонент собирается, если остальные компоненты комплекта уже там:
        ready_components — их номера.
        """

        if rework is not None:
            at, _ = self._operation(
                rework,
                item_id,
                at,
                [],
                None,
                previous_run=previous_run,
                reason="доработка по решению контролёра",
            )
            at = self.recheck(checkpoint, item_id, item_type, at + self._dur(60))
        nodes: list[Node] = []
        merge: Node | None = None
        for node in self.config.chain_from(checkpoint.node_id)[1:]:
            if len(self.config.predecessors(node.node_id)) > 1:
                merge = node
                break
            nodes.append(node)
        at, held = self._run(nodes, item_id, item_type, at, [], {})
        if held or merge is None or ready_components is None:
            return
        prefix = f"{self.config.line_id}-"
        index = item_id.removeprefix(prefix).rsplit("-", 1)[0]
        self._assemble(merge, index, [*ready_components, item_id], at, [], {})


def _decisions(planner: Planner, rng: random.Random, horizon: datetime) -> list[dict]:
    """Решения контролёров и технологов по обнаруженным несоответствиям истории."""

    steps: list[dict] = []
    seen: set[str] = set()
    for detection in sorted(planner.detections, key=lambda item: item["at"]):
        defect: Carried = detection["defect"]
        nc_id = nc_id_for(defect.item_id, defect.defect_type, defect.area)
        if nc_id in seen:
            continue
        seen.add(nc_id)
        at = detection["at"] + timedelta(minutes=rng.uniform(15, 150))
        if at > horizon:
            continue

        def decide(
            moment: datetime,
            action: str,
            user: str,
            reason: str,
            cause: str | None = None,
            nc_id: str = nc_id,
        ) -> None:
            steps.append(
                {
                    "kind": "decide",
                    "at": moment.isoformat(),
                    "nc_id": nc_id,
                    "action": action,
                    "user_id": user,
                    "reason": reason,
                    "cause_category": cause,
                }
            )

        if detection["confidence"] < 0.6:
            decide(at, "request_recheck", "controller", "уверенность анализатора ниже порога")
            if rng.random() < 0.6:
                decide(
                    at + timedelta(minutes=40),
                    "reject",
                    "controller",
                    "повторный осмотр признака не подтвердил",
                )
                continue
        if rng.random() < 0.12:
            continue
        decide(at + timedelta(minutes=45), "confirm", "controller", "признак подтверждён осмотром")
        if defect.origin.startswith("reworked:"):
            decide(
                at + timedelta(minutes=50),
                "confirm_cause",
                "technologist",
                "режим операции по техпроцессу нарушен",
                "process_issue",
            )
            decide(
                at + timedelta(minutes=55),
                "close",
                "controller",
                "устранено доработкой, повторный контроль чистый",
            )
            continue
        origin = planner.config.node(defect.origin)
        if origin.kind == "inspection":
            cause = "incoming_defect"
        elif defect.deviation:
            cause = "equipment_problem"
        else:
            roll = rng.random()
            cause = "process_issue" if roll < 0.45 else "operator_error" if roll < 0.65 else None
        if cause:
            decide(
                at + timedelta(minutes=80),
                "confirm_cause",
                "technologist",
                "разбор с участком",
                cause,
            )
    return steps


def history(config: LineConfig, sets: int, end: datetime, seed: int) -> list[dict]:
    """Шаги демонстрационной истории линии: доставки и решения, упорядоченные по времени."""

    rng = random.Random(seed)
    planner = Planner(config, rng, prefix="H")
    start = end - timedelta(seconds=config.takt_s * sets) - timedelta(hours=2)
    for index in range(sets):
        moment = start + timedelta(seconds=config.takt_s * index * rng.uniform(0.9, 1.1))
        planner.plan_set(moment, f"{index + 1:04d}")
    steps = []
    for at, message in planner.events:
        if at > end:
            continue
        delay = rng.uniform(0.5, 4.0)
        if rng.random() < 0.02:
            delay += rng.uniform(600, 2400)
        steps.append(
            {
                "kind": "deliver",
                "received_at": (at + timedelta(seconds=delay)).isoformat(),
                "event": message,
            }
        )
    steps += _decisions(planner, rng, end)
    steps.sort(key=lambda step: step.get("received_at") or step["at"])
    return steps


@dataclass
class LiveLine:
    """Состояние живой эмуляции одной линии."""

    running: bool = False
    speed: float = 60.0
    next_spawn: float = 0.0
    sets: int = 0
    queue: list[tuple[float, int, dict]] = field(default_factory=list)
    forced: dict[str, str] = field(default_factory=dict)
    planner: Planner | None = None
    # Прогон загруженного потока: конечное число изделий, спланированных заранее.
    run_total: int = 0
    run_items: int = 0
    run_started: float = 0.0
    run_duration_s: float = 0.0


class LiveEmulator:
    """Фоновый поток, который ведёт живые линии и отдаёт события в систему."""

    TICK_S = 0.5

    def __init__(self, ingest, configs) -> None:
        self._ingest = ingest
        self._configs = configs
        self._lines: dict[str, LiveLine] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._token = datetime.now(UTC).strftime("V%H%M%S")
        self._order = 0

    def status(self, line_id: str) -> dict:
        with self._lock:
            line = self._lines.get(line_id, LiveLine())
            run = None
            if line.run_total:
                delivered = line.run_total - len(line.queue)
                run = {
                    "items": line.run_items,
                    "events_total": line.run_total,
                    "events_delivered": delivered,
                    "progress": round(delivered / line.run_total, 3),
                    "finished": not line.queue,
                    "duration_s": line.run_duration_s,
                    "elapsed_s": round(time.time() - line.run_started, 1),
                }
            return {
                "running": line.running,
                "speed": line.speed,
                "sets_started": line.sets,
                "pending_events": len(line.queue),
                "forced": dict(line.forced),
                "run": run,
            }

    def start(self, line_id: str, speed: float | None = None) -> dict:
        with self._lock:
            line = self._lines.setdefault(line_id, LiveLine())
            line.running = True
            if speed:
                line.speed = max(1.0, min(float(speed), 600.0))
            line.next_spawn = 0.0
        self._ensure_thread()
        return self.status(line_id)

    def start_run(
        self,
        config: LineConfig,
        items: int,
        speed: float,
        duration_s: float,
        forced: dict[int, dict[str, str]],
        seed: int,
    ) -> dict:
        """Прогон загруженного потока: items изделий по такту, всё спланировано сразу."""

        now = time.time()
        with self._lock:
            line = self._lines.setdefault(config.line_id, LiveLine())
            line.running = False
            line.queue.clear()
            line.speed = speed
            self._runs = getattr(self, "_runs", 0) + 1
            # Номер прогона — время его запуска: у повторного прогона той же линии, в том
            # числе после перезапуска сервиса, изделия получают новые номера, а не сливаются
            # с изделиями прошлого прогона.
            stamp = datetime.fromtimestamp(now, UTC).strftime("%m%d%H%M%S")
            planner = Planner(
                config,
                random.Random(seed),
                prefix=f"{self._token}F{self._runs}",
                speed=speed,
                hold_unclear=True,
            )
            for index in range(items):
                start = datetime.fromtimestamp(now + index * config.takt_s / speed, UTC)
                planner.plan_set(start, f"{stamp}-{index + 1:03d}", forced.get(index + 1))
            for at, message in planner.events:
                self._order += 1
                heapq.heappush(line.queue, (at.timestamp(), self._order, message))
            line.run_total = len(planner.events)
            line.run_items = items
            line.run_started = now
            line.run_duration_s = duration_s
        self._ensure_thread()
        return self.status(config.line_id)

    def stop(self, line_id: str) -> dict:
        with self._lock:
            self._lines.setdefault(line_id, LiveLine()).running = False
        return self.status(line_id)

    def inject(self, line_id: str, node_id: str, kind: str) -> dict:
        with self._lock:
            self._lines.setdefault(line_id, LiveLine()).forced[node_id] = kind
        return self.status(line_id)

    def follow_up(self, line_id: str, plan) -> int:
        """
        События после решения человека: повторный контроль или продолжение изделия. plan
        получает планировщик и момент начала и пишет в него события; они уходят в систему
        по мере наступления, в темпе линии.
        """

        config = self._configs(line_id)
        if config is None:
            return 0
        with self._lock:
            line = self._lines.setdefault(line_id, LiveLine())
            self._follow_ups = getattr(self, "_follow_ups", 0) + 1
            planner = Planner(
                config,
                random.Random(),
                prefix=f"{self._token}D{self._follow_ups}",
                speed=line.speed,
                hold_unclear=True,
            )
            plan(planner, datetime.fromtimestamp(time.time() + 1, UTC))
            for at, message in planner.events:
                self._order += 1
                heapq.heappush(line.queue, (at.timestamp(), self._order, message))
        self._ensure_thread()
        return len(planner.events)

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="line-emulator", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.TICK_S):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — сбой одного такта не должен останавливать линию
                continue

    def tick(self, now: float | None = None) -> int:
        """Один такт: новые комплекты по темпу и доставка наступивших событий."""

        now = now or time.time()
        due: list[dict] = []
        with self._lock:
            for line_id, line in self._lines.items():
                config = self._configs(line_id)
                if config is None:
                    continue
                if line.running and now >= line.next_spawn:
                    if line.planner is None or line.planner.config.version != config.version:
                        line.planner = Planner(
                            config, random.Random(), prefix=f"{self._token}", hold_unclear=True
                        )
                    line.planner.speed = line.speed
                    line.planner.events.clear()
                    line.sets += 1
                    forced = dict(line.forced)
                    line.forced.clear()
                    line.planner.plan_set(
                        datetime.fromtimestamp(now, UTC), f"{self._token}{line.sets:03d}", forced
                    )
                    for at, message in line.planner.events:
                        self._order += 1
                        heapq.heappush(line.queue, (at.timestamp(), self._order, message))
                    line.next_spawn = now + config.takt_s / line.speed
                while line.queue and line.queue[0][0] <= now:
                    due.append(heapq.heappop(line.queue)[2])
        if due:
            self._ingest(due)
        return len(due)
