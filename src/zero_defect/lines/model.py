"""
Производственная линия как граф: узлы — этапы, рёбра — порядок процесса.

Узел — это либо операция (участок, оборудование, операция техпроцесса), либо контроль
(контрольная точка). Узел без входящих рёбер — вход материала на линию; узел с
несколькими входящими — сборка, где сходятся компоненты. Граф задаёт и то, как
эмулятор ведёт изделия, и то, как события настоящей линии раскладываются по этапам:
операция узнаётся по участку и операции техпроцесса, контроль — по контрольной точке.

Конфигурация линии хранится в базе (lines/store.py) и создаётся из интерфейса главной
ролью линии. Две линии ниже — демонстрационные; их параметры экономики условные.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from zero_defect.lines.equipment import SEED_TYPES

KINDS = ("operation", "inspection")
CHECKPOINT_KINDS = ("incoming", "after_operation", "final")


@dataclass
class Node:
    """Этап линии."""

    node_id: str
    title: str
    kind: str
    station_id: str
    x: float = 0.0
    y: float = 0.0
    duration_s: float = 600.0
    operation_id: str | None = None
    equipment_id: str | None = None
    # Тип станка и обработка на нём — из справочника оборудования (lines/equipment.py).
    machine_type: str | None = None
    processing: str | None = None
    operators: list[str] = field(default_factory=list)
    assembly: bool = False
    output_type: str | None = None
    checkpoint_id: str | None = None
    checkpoint_kind: str | None = None
    item_type_id: str | None = None
    origin: str = "manufactured"
    # Вероятность, что изделие получит дефект на этой операции или придёт с ним на входной
    # контроль. Параметр эмулятора, а не свойство реальной линии.
    defect_rate: float = 0.0
    defect_types: list[str] = field(default_factory=list)
    # Доля недостоверных наблюдений на контрольной точке: блик, загрязнение, расфокус.
    quality_issue_rate: float = 0.0
    # Дефекты, которые устраняются повторной операцией на этом узле, а не браком.
    rework_types: list[str] = field(default_factory=list)


@dataclass
class Economics:
    """Параметры экономики линии. Задаёт главная роль; в расчёт идут вместе с измерениями."""

    item_value_rub: float = 0.0
    rework_cost_rub: float = 0.0
    scrap_cost_rub: float = 0.0
    hour_cost_rub: float = 0.0
    shift_hours: float = 12.0
    note: str = ""


@dataclass
class LineConfig:
    """Линия целиком: граф, темп и экономика."""

    line_id: str
    title: str
    product_type_id: str
    nodes: list[Node]
    edges: list[tuple[str, str]]
    economics: Economics = field(default_factory=Economics)
    description: str = ""
    takt_s: float = 900.0
    version: int = 1

    # --- граф --------------------------------------------------------------------

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)

    def successors(self, node_id: str) -> list[str]:
        return [target for source, target in self.edges if source == node_id]

    def predecessors(self, node_id: str) -> list[str]:
        return [source for source, target in self.edges if target == node_id]

    def sources(self) -> list[Node]:
        targets = {target for _, target in self.edges}
        return [node for node in self.nodes if node.node_id not in targets]

    def chain_from(self, node_id: str) -> list[Node]:
        """Этапы от узла вниз по первому исходящему ребру до конца линии."""

        chain, current, seen = [], node_id, set()
        while current and current not in seen:
            seen.add(current)
            chain.append(self.node(current))
            following = self.successors(current)
            current = following[0] if following else None
        return chain

    def order(self) -> list[Node]:
        """Порядок процесса: топологическая сортировка, при равенстве — слева направо."""

        indegree = {node.node_id: 0 for node in self.nodes}
        for _, target in self.edges:
            indegree[target] += 1
        ready = sorted(
            (n for n in self.nodes if indegree[n.node_id] == 0), key=lambda n: (n.x, n.y)
        )
        result = []
        while ready:
            node = ready.pop(0)
            result.append(node)
            for target in self.successors(node.node_id):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(self.node(target))
                    ready.sort(key=lambda n: (n.x, n.y))
        return result

    def validate(self) -> list[str]:
        """Ошибки конфигурации словами; пустой список — линия корректна."""

        problems = []
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            problems.append("идентификаторы этапов повторяются")
        if not self.nodes:
            problems.append("в линии нет ни одного этапа")
        for source, target in self.edges:
            if source not in ids or target not in ids:
                problems.append(f"связь {source} → {target} ссылается на несуществующий этап")
        for node in self.nodes:
            if node.kind not in KINDS:
                problems.append(f"этап {node.node_id}: вид должен быть operation или inspection")
            if node.kind == "inspection" and node.checkpoint_kind not in CHECKPOINT_KINDS:
                problems.append(f"этап {node.node_id}: не указан вид контрольной точки")
            if node.kind == "operation" and not node.operation_id:
                problems.append(f"этап {node.node_id}: не указана операция техпроцесса")
            if not 0 <= node.defect_rate <= 1:
                problems.append(f"этап {node.node_id}: вероятность дефекта вне 0…1")
        if not problems and len(self.order()) != len(self.nodes):
            problems.append("в графе есть цикл: порядок процесса не определён")
        for source in self.sources() if not problems else []:
            if not source.item_type_id:
                problems.append(f"входной этап {source.node_id}: не указан тип изделия")
        return problems

    # --- события → этапы ------------------------------------------------------------

    def operation_node(self, station_id: str | None, operation_id: str | None) -> Node | None:
        for node in self.nodes:
            if node.kind == "operation" and node.station_id == station_id:
                if operation_id is None or node.operation_id == operation_id:
                    return node
        return None

    def inspection_node(self, checkpoint_id: str | None, item_type_id: str | None) -> Node | None:
        candidates = [
            n for n in self.nodes if n.kind == "inspection" and n.checkpoint_id == checkpoint_id
        ]
        for node in candidates:
            if node.item_type_id and node.item_type_id == item_type_id:
                return node
        generic = [node for node in candidates if not node.item_type_id]
        return (generic or candidates or [None])[0]

    def equipment_node(self, equipment_id: str | None) -> Node | None:
        for node in self.nodes:
            if node.equipment_id and node.equipment_id == equipment_id:
                return node
        return None

    def item_types(self) -> list[str]:
        types = [node.item_type_id for node in self.sources() if node.item_type_id]
        types += [node.output_type for node in self.nodes if node.output_type]
        return sorted(set(types))

    # --- хранение ------------------------------------------------------------------

    def to_json(self) -> str:
        data = asdict(self)
        data["edges"] = [list(edge) for edge in self.edges]
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict) -> LineConfig:
        return cls(
            line_id=data["line_id"],
            title=data["title"],
            product_type_id=data.get("product_type_id", ""),
            nodes=[_node(node) for node in data["nodes"]],
            edges=[tuple(edge) for edge in data["edges"]],
            economics=Economics(**data.get("economics", {})),
            description=data.get("description", ""),
            takt_s=float(data.get("takt_s", 900.0)),
            version=int(data.get("version", 1)),
        )


def _node(data: dict) -> Node:
    node = Node(**data)
    # Линии, сохранённые до справочника оборудования, типа станка не знают: он
    # восстанавливается по коду станка демонстрационных линий.
    if node.kind == "operation" and not node.machine_type:
        node.machine_type = SEED_TYPES.get(node.equipment_id or "")
    return node


def default_lines() -> list[LineConfig]:
    """Две демонстрационные линии. Узлы L1 совпадают с проверочными сценариями."""

    l1 = LineConfig(
        line_id="L1",
        title="Линия 1 · узел крепления УК-1",
        product_type_id="UNIT-U1",
        description="Корпус изготавливается и сваривается, фланец покупной; сборка и ОТК.",
        takt_s=720.0,
        nodes=[
            Node(
                "IN-B",
                "Входной контроль корпуса",
                "inspection",
                "ST-INC",
                60,
                120,
                240,
                checkpoint_id="CP-INC-01",
                checkpoint_kind="incoming",
                item_type_id="BODY-K1",
                defect_rate=0.02,
                defect_types=["INCLUSION"],
                quality_issue_rate=0.03,
            ),
            Node(
                "MILL",
                "Механообработка",
                "operation",
                "ST-MILL",
                240,
                120,
                1500,
                operation_id="OP-MILL-010",
                equipment_id="CNC-01",
                machine_type="cnc_mill",
                processing="фрезерование",
                operators=["OP-101", "OP-104"],
                defect_rate=0.06,
                defect_types=["BURR", "SCRATCH"],
                rework_types=["BURR"],
            ),
            Node(
                "QC-MILL",
                "Контроль после обработки",
                "inspection",
                "ST-MILL",
                410,
                120,
                180,
                checkpoint_id="CP-MILL-01",
                checkpoint_kind="after_operation",
                quality_issue_rate=0.04,
            ),
            Node(
                "WELD",
                "Сварка",
                "operation",
                "ST-WELD",
                580,
                120,
                1200,
                operation_id="OP-WELD-020",
                equipment_id="WELD-01",
                machine_type="welder",
                processing="аргонодуговая сварка",
                operators=["OP-102", "OP-105"],
                defect_rate=0.07,
                defect_types=["POROSITY", "LACK_OF_FUSION", "CRACK"],
            ),
            Node(
                "QC-WELD",
                "Контроль шва",
                "inspection",
                "ST-WELD",
                750,
                120,
                240,
                checkpoint_id="CP-WELD-01",
                checkpoint_kind="after_operation",
                quality_issue_rate=0.05,
            ),
            Node(
                "IN-F",
                "Входной контроль фланца",
                "inspection",
                "ST-INC",
                60,
                330,
                180,
                checkpoint_id="CP-INC-01",
                checkpoint_kind="incoming",
                item_type_id="FLANGE-F2",
                origin="purchased",
                defect_rate=0.03,
                defect_types=["DENT"],
                quality_issue_rate=0.02,
            ),
            Node(
                "ASM",
                "Сборка узла",
                "operation",
                "ST-ASM",
                750,
                330,
                900,
                operation_id="OP-ASM-030",
                equipment_id="ASM-TOOL-01",
                machine_type="assembly",
                processing="резьбовая сборка",
                operators=["OP-103"],
                assembly=True,
                output_type="UNIT-U1",
                defect_rate=0.02,
                defect_types=["DENT"],
            ),
            Node(
                "FINAL",
                "Финальный контроль ОТК",
                "inspection",
                "ST-QC",
                920,
                330,
                300,
                checkpoint_id="CP-FINAL-01",
                checkpoint_kind="final",
                quality_issue_rate=0.02,
            ),
        ],
        edges=[
            ("IN-B", "MILL"),
            ("MILL", "QC-MILL"),
            ("QC-MILL", "WELD"),
            ("WELD", "QC-WELD"),
            ("QC-WELD", "ASM"),
            ("IN-F", "ASM"),
            ("ASM", "FINAL"),
        ],
        economics=Economics(
            item_value_rub=180_000,
            rework_cost_rub=6_500,
            scrap_cost_rub=140_000,
            hour_cost_rub=4_200,
            shift_hours=12,
            note="Условные значения для демонстрации; задаются руководителем производства.",
        ),
    )
    l2 = LineConfig(
        line_id="L2",
        title="Линия 2 · кронштейн КР-2",
        product_type_id="BRACKET-B2",
        description="Лист, лазерная резка, гибка, покрытие, ОТК.",
        takt_s=480.0,
        nodes=[
            Node(
                "L2-IN",
                "Входной контроль листа",
                "inspection",
                "ST2-INC",
                60,
                220,
                120,
                checkpoint_id="CP2-INC-01",
                checkpoint_kind="incoming",
                item_type_id="BRACKET-B2",
                defect_rate=0.02,
                defect_types=["LAMINATION"],
                quality_issue_rate=0.02,
            ),
            Node(
                "L2-CUT",
                "Лазерная резка",
                "operation",
                "ST2-CUT",
                230,
                220,
                420,
                operation_id="OP2-CUT-010",
                equipment_id="LASER-01",
                machine_type="laser",
                processing="лазерная резка",
                operators=["OP-201", "OP-202"],
                defect_rate=0.04,
                defect_types=["BURR", "DROSS"],
                rework_types=["BURR", "DROSS"],
            ),
            Node(
                "L2-BEND",
                "Гибка",
                "operation",
                "ST2-BEND",
                400,
                220,
                360,
                operation_id="OP2-BEND-020",
                equipment_id="PRESS-01",
                machine_type="press_brake",
                processing="гибка",
                operators=["OP-203"],
                defect_rate=0.05,
                defect_types=["CRACK", "ANGLE_DEVIATION"],
            ),
            Node(
                "L2-QC",
                "Контроль геометрии",
                "inspection",
                "ST2-BEND",
                570,
                220,
                180,
                checkpoint_id="CP2-GEOM-01",
                checkpoint_kind="after_operation",
                quality_issue_rate=0.03,
            ),
            Node(
                "L2-PAINT",
                "Покрытие",
                "operation",
                "ST2-PAINT",
                740,
                220,
                600,
                operation_id="OP2-PAINT-030",
                equipment_id="PAINT-01",
                machine_type="paint_booth",
                processing="окраска",
                operators=["OP-204"],
                defect_rate=0.05,
                defect_types=["COATING_GAP", "SAG"],
                rework_types=["SAG"],
            ),
            Node(
                "L2-FINAL",
                "Финальный контроль ОТК",
                "inspection",
                "ST2-QC",
                910,
                220,
                240,
                checkpoint_id="CP2-FINAL-01",
                checkpoint_kind="final",
                quality_issue_rate=0.02,
            ),
        ],
        edges=[
            ("L2-IN", "L2-CUT"),
            ("L2-CUT", "L2-BEND"),
            ("L2-BEND", "L2-QC"),
            ("L2-QC", "L2-PAINT"),
            ("L2-PAINT", "L2-FINAL"),
        ],
        economics=Economics(
            item_value_rub=24_000,
            rework_cost_rub=1_200,
            scrap_cost_rub=17_000,
            hour_cost_rub=3_100,
            shift_hours=12,
            note="Условные значения для демонстрации; задаются руководителем производства.",
        ),
    )
    return [l1, l2]
