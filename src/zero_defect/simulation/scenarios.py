"""
Восемь проверочных сценариев постановки (раздел 4.2) и демонстрационный набор.

Каждый сценарий — функция, дописывающая шаги в построитель. Шаги — доставки сообщений
с моментом поступления, решения людей и контрольное изменение записи. Ожидаемый итог
каждого сценария записан отдельно от входа, в data/scenarios/<имя>/expected.json, и
составлен руками по смыслу сценария, а не выгружен из системы: иначе проверка
подтверждала бы сама себя.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta

from zero_defect.simulation.line import MSK, Builder, defect, standard_unit


def normal_production(b: Builder) -> None:
    """Два узла без отклонений; второй корпус фрезерует другой оператор, с паузой."""

    standard_unit(b, "0101")
    b.register("B-0102", "BODY-K1", "manufactured", "WO-0102")
    b.register("F-0102", "FLANGE-F2", "purchased", "WO-0102")
    b.register("U-0102", "UNIT-U1", "manufactured", "WO-0102")
    b.advance(5)
    b.inspect("B-0102", "incoming")
    b.inspect("F-0102", "incoming")
    b.advance(10)
    mill = b.start("B-0102", "mill", "OP-104")
    b.machine("CNC-01", "running")
    b.advance(12)
    b.emit(
        "operation_paused",
        "term-mill",
        item_id="B-0102",
        operation_run_id=mill,
        pause_reason="замена фрезы",
    )
    b.advance(6)
    b.emit("operation_resumed", "term-mill", item_id="B-0102", operation_run_id=mill)
    b.advance(15)
    b.finish("B-0102", "mill", mill, 27)
    b.advance(2)
    b.inspect("B-0102", "mill", run_id=mill)
    b.advance(8)
    weld = b.operate("B-0102", "weld", "OP-102", 21)
    b.inspect("B-0102", "weld", run_id=weld)
    b.advance(6)
    assembly = b.start("U-0102", "assembly", "OP-103")
    b.link("U-0102", "B-0102", assembly)
    b.link("U-0102", "F-0102", assembly)
    b.advance(14)
    b.finish("U-0102", "assembly", assembly, 14)
    b.advance(5)
    b.inspect("U-0102", "final", run_id=assembly)
    b.advance(10)


def incoming_defect(b: Builder) -> None:
    """Включение в материале заготовки найдено на входном контроле и позже снова."""

    b.register("B-0201", "BODY-K1", "manufactured", "WO-0201")
    b.advance(5)
    found = defect("INCLUSION", "торец", "major", "неметаллическое включение на торце заготовки")
    b.inspect("B-0201", "incoming", "defect_signs_found", defects=[found], confidence=0.91)
    b.advance(15)
    mill = b.operate("B-0201", "mill", "OP-101", 24)
    b.inspect(
        "B-0201", "mill", "defect_signs_found", defects=[dict(found)], confidence=0.89, run_id=mill
    )
    b.advance(10)


def defect_after_operation(b: Builder) -> None:
    """Пористость шва после сварки при чистом контроле после фрезерования."""

    b.register("B-0301", "BODY-K1", "manufactured", "WO-0301")
    b.advance(5)
    b.inspect("B-0301", "incoming")
    b.advance(10)
    mill = b.operate("B-0301", "mill", "OP-101", 25)
    b.inspect("B-0301", "mill", run_id=mill)
    b.advance(8)
    weld = b.operate("B-0301", "weld", "OP-102", 22)
    photo = [
        {
            "uri": "cam://ST-WELD/CP-WELD-01/B-0301/0001.jpg",
            "kind": "photo",
            "captured_at": b.clock.isoformat(),
            "checkpoint_id": "CP-WELD-01",
        }
    ]
    found = defect("POROSITY", "шов 1", "major", "поры в корне шва, скопление")
    b.inspect(
        "B-0301",
        "weld",
        "defect_signs_found",
        defects=[found],
        confidence=0.88,
        run_id=weld,
        evidence=photo,
    )
    b.advance(10)


def insufficient_information(b: Builder) -> None:
    """Трещина найдена на финальном контроле, а все предыдущие проверки недостоверны."""

    b.register("B-0401", "BODY-K1", "manufactured", "WO-0401")
    b.register("F-0401", "FLANGE-F2", "purchased", "WO-0401")
    b.register("U-0401", "UNIT-U1", "manufactured", "WO-0401")
    b.advance(5)
    b.inspect("B-0401", "incoming", "not_assessable", quality="poor", confidence=0.2)
    b.inspect("F-0401", "incoming")
    b.advance(10)
    mill = b.operate("B-0401", "mill", "OP-101", 25)
    b.inspect("B-0401", "mill", quality="degraded", confidence=0.42, run_id=mill)
    b.advance(8)
    weld = b.start("B-0401", "weld", "OP-102")
    b.machine("WELD-01", "running")
    b.advance(21)
    b.finish("B-0401", "weld", weld, 21)
    b.action(
        "OP-102",
        "check_skipped",
        weld,
        "контроль после сварки не выполнен: камера занята",
        "ST-WELD",
    )
    b.advance(6)
    assembly = b.start("U-0401", "assembly", "OP-103")
    b.link("U-0401", "B-0401", assembly)
    b.link("U-0401", "F-0401", assembly)
    b.advance(15)
    b.finish("U-0401", "assembly", assembly, 15)
    b.advance(5)
    crack = defect(
        "CRACK", "ребро жёсткости", "critical", "трещина у основания ребра", component="B-0401"
    )
    b.inspect(
        "U-0401", "final", "defect_signs_found", defects=[crack], confidence=0.86, run_id=assembly
    )
    b.advance(10)


def equipment_deviation(b: Builder) -> None:
    """Выход сварочного тока за допуск во время сварки и непровар на контроле после неё."""

    b.register("B-0501", "BODY-K1", "manufactured", "WO-0501")
    b.advance(5)
    b.inspect("B-0501", "incoming")
    b.advance(10)
    mill = b.operate("B-0501", "mill", "OP-101", 25)
    b.inspect("B-0501", "mill", run_id=mill)
    b.advance(8)
    weld = b.start("B-0501", "weld", "OP-102")
    b.machine("WELD-01", "running", current_a=175.0)
    b.advance(9)
    b.machine("WELD-01", "deviation", "ток сварки 212 А при допуске 160–190 А", current_a=212.0)
    b.advance(4)
    b.machine("WELD-01", "running", current_a=178.0)
    b.advance(8)
    b.finish("B-0501", "weld", weld, 21)
    b.advance(2)
    found = defect("LACK_OF_FUSION", "шов 2", "critical", "непровар по длине 6 мм")
    b.inspect("B-0501", "weld", "defect_signs_found", defects=[found], confidence=0.9, run_id=weld)
    b.advance(10)
    b.machine("CNC-01", "warning", "плановое предупреждение: ресурс шпинделя 90 %")
    b.advance(5)


def repeat_and_late_delivery(b: Builder) -> None:
    """Нормальный узел, доставленный с повторами, опозданием, пропуском и браком формата."""

    body, flange, unit = "B-0601", "F-0601", "U-0601"
    b.register(body, "BODY-K1", "manufactured", "WO-0601")
    registration = b.steps[-1]["event"]
    b.register(flange, "FLANGE-F2", "purchased", "WO-0601")
    b.register(unit, "UNIT-U1", "manufactured", "WO-0601")
    b.advance(5)
    b.inspect(body, "incoming")
    b.inspect(flange, "incoming")
    b.advance(10)
    mill = b.operate(body, "mill", "OP-101", 25)
    inspection = b.inspect(body, "mill", run_id=mill)
    # Повторная доставка того же сообщения — источник не получил подтверждения.
    b.deliver(copy.deepcopy(inspection), delay_min=2.2)
    b.advance(8)
    weld = b.start(body, "weld", "OP-102")
    b.machine("WELD-01", "running", current_a=176.0)
    b.advance(10)
    # Сообщение станка сформировано, но потеряно: в нумерации источника будет пропуск.
    b.event(
        "machine_state",
        "mlog-weld01",
        equipment_id="WELD-01",
        line_id="L1",
        machine_state="running",
    )
    b.advance(10)
    # Завершение сварки дошло через 45 минут, уже после контроля и сборки.
    b.finish(body, "weld", weld, 20, delay_min=45)
    # Следующее сообщение того же станка дошло: по его номеру виден пропуск.
    b.machine("WELD-01", "idle")
    b.advance(2)
    b.inspect(body, "weld", run_id=weld)
    b.advance(6)
    assembly = b.start(unit, "assembly", "OP-103")
    b.link(unit, body, assembly)
    b.link(unit, flange, assembly)
    # Сообщение версии 1.1 со сменой от источника и с полем, которого нет в контракте.
    message = b.event(
        "operator_action",
        "opvision-01",
        version="1.1",
        operator_id="OP-103",
        action_type="confirmation",
        operation_run_id=assembly,
        station_id="ST-ASM",
        details="подтверждение установки фланца",
        shift_id="S1",
    )
    message["humidity_pct"] = 41
    b.deliver(message)
    b.advance(15)
    b.finish(unit, "assembly", assembly, 15)
    b.advance(5)
    b.inspect(unit, "final", run_id=assembly)
    b.advance(3)
    # Четыре сообщения, которые обязаны попасть в карантин, а не в историю.
    broken = b.event(
        "inspection_reported",
        "vision-final",
        item_id=unit,
        checkpoint_id="CP-FINAL-01",
        inspection_result="no_defect_signs",
    )
    b.deliver(broken)
    future = b.event("machine_state", "mlog-cnc01", equipment_id="CNC-01", machine_state="running")
    future["schema_version"] = "2.0"
    b.deliver(future)
    odd = b.event("machine_state", "mlog-cnc01", equipment_id="CNC-01", machine_state="overheated")
    b.deliver(odd)
    conflict = copy.deepcopy(registration)
    conflict["item_type_id"] = "BODY-K9"
    b.deliver(conflict)
    b.advance(10)


def controller_decision(b: Builder) -> None:
    """Заусенец подтверждён и устранён доработкой, сомнительная царапина отклонена."""

    body, flange, unit = "B-0701", "F-0701", "U-0701"
    b.register(body, "BODY-K1", "manufactured", "WO-0701")
    b.register(flange, "FLANGE-F2", "purchased", "WO-0701")
    b.register(unit, "UNIT-U1", "manufactured", "WO-0701")
    b.advance(5)
    b.inspect(body, "incoming")
    b.inspect(flange, "incoming")
    b.advance(10)
    first = b.operate(body, "mill", "OP-101", 26)
    burr = defect("BURR", "кромка отверстия", "major", "заусенец по кромке отверстия")
    scratch = defect("SCRATCH", "плоскость А", "minor", "возможная риска на плоскости")
    b.inspect(
        body, "mill", "defect_signs_found", defects=[burr, scratch], confidence=0.58, run_id=first
    )
    b.advance(5)
    b.decide(
        body,
        "BURR",
        "кромка отверстия",
        "confirm",
        "ctrl-01",
        "заусенец подтверждён осмотром, высота 0,4 мм",
    )
    b.advance(1)
    b.decide(
        body,
        "SCRATCH",
        "плоскость А",
        "request_recheck",
        "ctrl-01",
        "уверенность анализатора ниже порога",
    )
    b.advance(5)
    rework = b.operate(
        body, "mill", "OP-101", 8, previous_run_id=first, rework_reason="удаление заусенца по NC"
    )
    b.inspect(body, "mill", run_id=rework, confidence=0.96)
    b.advance(3)
    b.decide(
        body,
        "SCRATCH",
        "плоскость А",
        "reject",
        "ctrl-01",
        "повторный контроль признаков не выявил",
    )
    b.decide(
        body,
        "BURR",
        "кромка отверстия",
        "confirm_cause",
        "tech-01",
        "нарушен режим снятия фаски, предусмотренный техпроцессом",
        cause_category="operator_error",
    )
    b.decide(
        body,
        "BURR",
        "кромка отверстия",
        "close",
        "ctrl-01",
        "устранено доработкой, повторный контроль чистый",
    )
    b.advance(8)
    weld = b.operate(body, "weld", "OP-102", 20)
    b.inspect(body, "weld", run_id=weld)
    b.advance(6)
    assembly = b.start(unit, "assembly", "OP-103")
    b.link(unit, body, assembly)
    b.link(unit, flange, assembly)
    b.advance(15)
    b.finish(unit, "assembly", assembly, 15)
    b.advance(5)
    b.inspect(unit, "final", run_id=assembly)
    b.advance(10)


def integrity_tamper(b: Builder) -> None:
    """Контрольное изменение сохранённой записи в обход приложения."""

    b.register("B-0801", "BODY-K1", "manufactured", "WO-0801")
    b.advance(5)
    b.inspect("B-0801", "incoming")
    b.advance(10)
    mill = b.operate("B-0801", "mill", "OP-101", 25)
    target = b.inspect("B-0801", "mill", run_id=mill)
    b.advance(5)
    b.tamper(target["event_id"])


SCENARIOS: dict[str, tuple[str, Callable[[Builder], None]]] = {
    "01_normal_production": ("Нормальное изготовление", normal_production),
    "02_incoming_defect": ("Входной дефект", incoming_defect),
    "03_defect_after_operation": ("Дефект после операции", defect_after_operation),
    "04_insufficient_information": ("Недостаток сведений для вывода", insufficient_information),
    "05_equipment_deviation": ("Отклонение оборудования", equipment_deviation),
    "06_repeat_and_late_delivery": ("Повторная и поздняя доставка", repeat_and_late_delivery),
    "07_controller_decision": ("Решение контролёра", controller_decision),
    "08_integrity_tamper": ("Контрольное изменение записи", integrity_tamper),
}

START = datetime(2026, 9, 25, 8, 0, tzinfo=MSK)


def build(name: str) -> list[dict]:
    """Шаги одного сценария; у каждого сценария своя нумерация событий."""

    _, function = SCENARIOS[name]
    builder = Builder(clock=START, prefix=f"S{name[:2]}")
    function(builder)
    return _ordered(builder.steps)


def build_demo() -> list[dict]:
    """Все сценарии, кроме изменения записи, подряд на одной линии со сдвигом по времени.

    Изменение записи в демонстрационный набор не входит: его показывают отдельно
    командой scripts/tamper.py, чтобы журнал демонстрации оставался целым до показа.
    """

    steps: list[dict] = []
    # Нумерация у источника одна на всю линию: иначе номера соседних сценариев
    # перекрывали бы друг друга и скрыли пропуск, который показывает сценарий 06.
    sequences: dict[str, int] = defaultdict(int)
    for index, name in enumerate(SCENARIOS):
        if name == "08_integrity_tamper":
            continue
        builder = Builder(clock=START + timedelta(hours=5 * index), prefix=f"S{name[:2]}")
        builder._sequences = sequences
        SCENARIOS[name][1](builder)
        steps.extend(builder.steps)
    return _ordered(steps)


def _ordered(steps: list[dict]) -> list[dict]:
    def moment(step: dict) -> datetime:
        return datetime.fromisoformat(step.get("received_at") or step["at"])

    return sorted(steps, key=moment)
