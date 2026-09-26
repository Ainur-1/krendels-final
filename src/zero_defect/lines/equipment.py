"""
Справочник оборудования: типы станков и зарегистрированные станки.

Тип станка определяет, какую обработку он выполняет, какие дефекты на нём возникают и
какие параметры режима пишет в MachineLogs с допусками. Станок — экземпляр типа с
кодом, названием и своими допусками. Новый станок заводит администратор; руководитель
в редакторе линии выбирает станок только из справочника, и этапу подтягиваются
обработка и дефекты его типа.

Допуски ниже условные, для демонстрации: на предприятии их берут из паспорта станка и
техпроцесса, и администратор задаёт их у каждого станка.
"""

from __future__ import annotations

import json
import random
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import insert, select, update

from zero_defect.lines.catalog import DEFECTS
from zero_defect.storage.database import Database
from zero_defect.storage.database import equipment as equipment_table


def _param(title: str, unit: str, low: float, high: float) -> dict:
    return {"title": title, "unit": unit, "low": low, "high": high}


MACHINE_TYPES: dict[str, dict] = {
    "cnc_mill": {
        "title": "Фрезерный станок с ЧПУ",
        "processing": ["фрезерование", "сверление", "растачивание"],
        "defect_types": ["BURR", "SCRATCH", "DENT"],
        "parameters": {
            "spindle_load_pct": _param("Нагрузка шпинделя", "%", 20, 85),
            "vibration_mm_s": _param("Вибрация", "мм/с", 0, 4.5),
            "coolant_temp_c": _param("Температура СОЖ", "°C", 18, 30),
        },
    },
    "lathe": {
        "title": "Токарный станок с ЧПУ",
        "processing": ["точение", "нарезание резьбы", "растачивание"],
        "defect_types": ["BURR", "SCRATCH"],
        "parameters": {
            "spindle_load_pct": _param("Нагрузка шпинделя", "%", 20, 85),
            "vibration_mm_s": _param("Вибрация", "мм/с", 0, 4.5),
            "coolant_temp_c": _param("Температура СОЖ", "°C", 18, 30),
        },
    },
    "welder": {
        "title": "Установка аргонодуговой сварки",
        "processing": ["аргонодуговая сварка", "прихватка"],
        "defect_types": ["POROSITY", "LACK_OF_FUSION", "CRACK"],
        "parameters": {
            "current_a": _param("Ток сварки", "А", 160, 190),
            "arc_voltage_v": _param("Напряжение дуги", "В", 11, 14),
            "gas_flow_l_min": _param("Расход аргона", "л/мин", 8, 12),
        },
    },
    "assembly": {
        "title": "Сборочный стенд",
        "processing": ["резьбовая сборка", "запрессовка"],
        "defect_types": ["DENT", "SCRATCH"],
        "parameters": {
            "torque_nm": _param("Момент затяжки", "Н·м", 18, 22),
            "press_force_kn": _param("Усилие запрессовки", "кН", 4, 6),
        },
    },
    "laser": {
        "title": "Установка лазерной резки",
        "processing": ["лазерная резка"],
        "defect_types": ["BURR", "DROSS"],
        "parameters": {
            "laser_power_kw": _param("Мощность излучения", "кВт", 2.8, 3.2),
            "cut_speed_m_min": _param("Скорость реза", "м/мин", 2.5, 3.5),
            "gas_pressure_bar": _param("Давление газа", "бар", 12, 16),
        },
    },
    "press_brake": {
        "title": "Листогибочный пресс",
        "processing": ["гибка", "отбортовка"],
        "defect_types": ["CRACK", "ANGLE_DEVIATION"],
        "parameters": {
            "bend_force_kn": _param("Усилие гиба", "кН", 300, 420),
            "ram_speed_mm_s": _param("Скорость траверсы", "мм/с", 8, 12),
        },
    },
    "paint_booth": {
        "title": "Окрасочная камера",
        "processing": ["окраска", "грунтование"],
        "defect_types": ["COATING_GAP", "SAG"],
        "parameters": {
            "booth_temp_c": _param("Температура в камере", "°C", 18, 25),
            "humidity_pct": _param("Влажность", "%", 40, 65),
            "film_um": _param("Толщина слоя", "мкм", 60, 90),
        },
    },
}

STATUSES = ("active", "maintenance", "retired")

# Станки демонстрационных линий. По этой таблице восстанавливается тип станка у линий,
# сохранённых до появления справочника: в их конфигурации типа ещё нет.
SEED = [
    ("CNC-01", "Фрезерный центр ЧПУ №1", "cnc_mill", "ИН-1001"),
    ("CNC-02", "Фрезерный центр ЧПУ №2", "cnc_mill", "ИН-1002"),
    ("LATHE-01", "Токарный центр ЧПУ №1", "lathe", "ИН-1010"),
    ("WELD-01", "Аргонодуговая установка №1", "welder", "ИН-2001"),
    ("WELD-02", "Аргонодуговая установка №2", "welder", "ИН-2002"),
    ("ASM-TOOL-01", "Сборочный стенд №1", "assembly", "ИН-3001"),
    ("LASER-01", "Лазерный комплекс №1", "laser", "ИН-4001"),
    ("PRESS-01", "Листогиб №1", "press_brake", "ИН-5001"),
    ("PAINT-01", "Окрасочная камера №1", "paint_booth", "ИН-6001"),
]
SEED_TYPES = {equipment_id: machine_type for equipment_id, _, machine_type, _ in SEED}

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")


class EquipmentError(ValueError):
    """Станок описан некорректно или не найден."""


@dataclass
class Machine:
    """Зарегистрированный станок."""

    equipment_id: str
    title: str
    machine_type: str
    inventory_no: str = ""
    status: str = "active"
    defect_types: list[str] = field(default_factory=list)
    parameters: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def of_type(cls, equipment_id: str, title: str, machine_type: str, **extra) -> Machine:
        """Станок с дефектами и допусками своего типа по умолчанию."""

        spec = MACHINE_TYPES[machine_type]
        return cls(
            equipment_id=equipment_id,
            title=title,
            machine_type=machine_type,
            defect_types=list(spec["defect_types"]),
            parameters={key: dict(value) for key, value in spec["parameters"].items()},
            **extra,
        )

    def validate(self) -> list[str]:
        problems = []
        if not ID_PATTERN.match(self.equipment_id):
            problems.append("код станка: латиница, цифры, дефис; до 48 знаков")
        if not self.title.strip():
            problems.append("не указано название станка")
        if self.machine_type not in MACHINE_TYPES:
            problems.append(f"тип станка {self.machine_type!r} неизвестен")
        if self.status not in STATUSES:
            problems.append(f"состояние {self.status!r} неизвестно")
        unknown = [code for code in self.defect_types if code not in DEFECTS]
        if unknown:
            problems.append(f"виды дефектов не из справочника: {', '.join(unknown)}")
        for key, spec in self.parameters.items():
            try:
                low, high = float(spec["low"]), float(spec["high"])
            except (KeyError, TypeError, ValueError):
                problems.append(f"параметр {key}: нужны числа low и high")
                continue
            if low >= high:
                problems.append(f"параметр {key}: нижняя граница допуска не меньше верхней")
        return problems

    def to_dict(self) -> dict:
        data = asdict(self)
        spec = MACHINE_TYPES.get(self.machine_type, {})
        data["type_title"] = spec.get("title", self.machine_type)
        data["processing"] = spec.get("processing", [])
        return data


class EquipmentStore:
    """Станки в базе: чтение, регистрация, изменение."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = threading.Lock()
        self._cache: dict[str, Machine] = {}
        with database.engine.begin() as conn:
            if conn.execute(select(equipment_table.c.equipment_id).limit(1)).first() is None:
                now = datetime.now(UTC).isoformat()
                conn.execute(
                    insert(equipment_table),
                    [
                        {
                            "equipment_id": equipment_id,
                            "config": json.dumps(
                                asdict(
                                    Machine.of_type(
                                        equipment_id, title, kind, inventory_no=inventory
                                    )
                                ),
                                ensure_ascii=False,
                            ),
                            "updated_at": now,
                            "updated_by": "system",
                        }
                        for equipment_id, title, kind, inventory in SEED
                    ],
                )
        self.reload()

    def reload(self) -> None:
        with self.database.engine.connect() as conn:
            rows = conn.execute(select(equipment_table.c.config)).scalars().all()
        with self._lock:
            self._cache = {}
            for raw in rows:
                machine = Machine(**json.loads(raw))
                self._cache[machine.equipment_id] = machine

    def all(self) -> list[Machine]:
        with self._lock:
            return sorted(self._cache.values(), key=lambda machine: machine.equipment_id)

    def get(self, equipment_id: str | None) -> Machine | None:
        with self._lock:
            return self._cache.get(equipment_id or "")

    def save(self, machine: Machine, author: str, create: bool) -> Machine:
        problems = machine.validate()
        if problems:
            raise EquipmentError("; ".join(problems))
        exists = self.get(machine.equipment_id) is not None
        if create and exists:
            raise EquipmentError(f"станок {machine.equipment_id} уже зарегистрирован")
        if not create and not exists:
            raise EquipmentError(f"станок {machine.equipment_id} не найден")
        values = {
            "config": json.dumps(asdict(machine), ensure_ascii=False),
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": author,
        }
        with self.database.engine.begin() as conn:
            if create:
                conn.execute(
                    insert(equipment_table).values(equipment_id=machine.equipment_id, **values)
                )
            else:
                conn.execute(
                    update(equipment_table)
                    .where(equipment_table.c.equipment_id == machine.equipment_id)
                    .values(**values)
                )
        self.reload()
        return self.get(machine.equipment_id)


def machine_parameters(machine_type: str | None, seed: str, deviation: bool) -> dict[str, float]:
    """
    Показания станка для эмулятора: в режиме — внутри допуска, при отклонении один
    параметр выходит за него на 8–25 %. Генератор свой, от идентификатора операции:
    основной поток случайных чисел эмулятора не сдвигается, и история линий из того же
    зерна остаётся прежней.
    """

    spec = MACHINE_TYPES.get(machine_type or "")
    if not spec:
        return {}
    rng = random.Random(seed)
    values = {}
    for key, param in spec["parameters"].items():
        low, high = param["low"], param["high"]
        values[key] = round(rng.uniform(low + (high - low) * 0.2, high - (high - low) * 0.2), 2)
    if deviation:
        key = rng.choice(sorted(spec["parameters"]))
        low, high = spec["parameters"][key]["low"], spec["parameters"][key]["high"]
        excess = (high - low) * rng.uniform(0.08, 0.25)
        # У параметра с нулевой нижней границей (вибрация) выход бывает только вверх.
        above = low <= 0 or rng.random() < 0.5
        values[key] = round(high + excess if above else low - excess, 2)
    return values
