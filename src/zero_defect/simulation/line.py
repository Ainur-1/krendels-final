"""
Условная производственная линия и построитель потока её событий.

Изделие, участки, операции и допуски условные — реальных данных предприятия нет и не
требуется. Линия выбрана так, чтобы на ней встречались все ситуации постановки:
покупной компонент с входным контролем, два изготавливающих участка с разными
операторами и оборудованием, сборка и финальный контроль.

    корпус BODY-K1 ─ входной контроль ─ фрезерование ─ контроль ─ сварка ─ контроль ─┐
    фланец FLANGE-F2 (покупной) ─ входной контроль ─────────────────────────────────┤
                                                        сборка узла UNIT-U1 ─ финальный контроль
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from zero_defect.quality.nonconformance import nc_id_for

MSK = timezone(timedelta(hours=3))
LINE = "L1"
ANALYZER = "visionqc-2.3.1"

STATIONS = {
    "incoming": "ST-INC",
    "mill": "ST-MILL",
    "weld": "ST-WELD",
    "assembly": "ST-ASM",
    "final": "ST-QC",
}
OPERATIONS = {"mill": "OP-MILL-010", "weld": "OP-WELD-020", "assembly": "OP-ASM-030"}
EQUIPMENT = {"mill": "CNC-01", "weld": "WELD-01", "assembly": "ASM-TOOL-01"}
MACHINE_SOURCES = {"CNC-01": "mlog-cnc01", "WELD-01": "mlog-weld01", "ASM-TOOL-01": "mlog-asm01"}
CHECKPOINTS = {
    "incoming": ("CP-INC-01", "incoming", "vision-inc"),
    "mill": ("CP-MILL-01", "after_operation", "vision-mill"),
    "weld": ("CP-WELD-01", "after_operation", "vision-weld"),
    "final": ("CP-FINAL-01", "final", "vision-final"),
}
TERMINALS = {"mill": "term-mill", "weld": "term-weld", "assembly": "term-asm"}


@dataclass
class Builder:
    """Порождает события и шаги сценария с детерминированными идентификаторами."""

    clock: datetime
    prefix: str = "EV"
    steps: list[dict] = field(default_factory=list)
    _counter: int = 0
    _sequences: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _runs: int = 0

    # --- время -------------------------------------------------------------------

    def advance(self, minutes: float) -> datetime:
        self.clock += timedelta(minutes=minutes)
        return self.clock

    # --- события -----------------------------------------------------------------

    def event(self, event_type: str, source_id: str, *, version: str = "1.0", **fields) -> dict:
        self._counter += 1
        self._sequences[source_id] += 1
        message = {
            "event_id": f"{self.prefix}-{self._counter:05d}",
            "event_type": event_type,
            "schema_version": version,
            "occurred_at": self.clock.isoformat(),
            "source_id": source_id,
            "sequence_no": self._sequences[source_id],
        }
        message.update({key: value for key, value in fields.items() if value is not None})
        return message

    def deliver(self, message: dict, delay_min: float = 0.2) -> dict:
        """Доставка сообщения. delay_min — задержка от возникновения до поступления."""

        occurred = datetime.fromisoformat(message["occurred_at"])
        received = occurred + timedelta(minutes=delay_min)
        self.steps.append(
            {"kind": "deliver", "received_at": received.isoformat(), "event": message}
        )
        return message

    def emit(self, event_type: str, source_id: str, *, delay_min: float = 0.2, **fields) -> dict:
        return self.deliver(self.event(event_type, source_id, **fields), delay_min)

    # --- производственные шаги ------------------------------------------------------

    def register(self, item_id: str, item_type_id: str, origin: str, work_order: str) -> None:
        self.emit(
            "item_registered",
            "mes-gw",
            item_id=item_id,
            item_type_id=item_type_id,
            line_id=LINE,
            work_order_id=work_order,
            origin=origin,
        )

    def inspect(
        self,
        item_id: str,
        stage: str,
        result: str = "no_defect_signs",
        *,
        defects: list[dict] | None = None,
        quality: str = "good",
        confidence: float = 0.95,
        run_id: str | None = None,
        evidence: list[dict] | None = None,
        delay_min: float = 0.2,
    ) -> dict:
        checkpoint, kind, source = CHECKPOINTS[stage]
        return self.emit(
            "inspection_reported",
            source,
            delay_min=delay_min,
            item_id=item_id,
            line_id=LINE,
            station_id=STATIONS[stage],
            checkpoint_id=checkpoint,
            checkpoint_kind=kind,
            operation_run_id=run_id,
            inspection_result=result,
            defects=defects,
            confidence=confidence,
            observation_quality=quality,
            analyzer_version=ANALYZER,
            evidence_refs=evidence,
        )

    def machine(self, equipment: str, state: str, message: str | None = None, **parameters) -> dict:
        return self.emit(
            "machine_state",
            MACHINE_SOURCES[equipment],
            equipment_id=equipment,
            line_id=LINE,
            machine_state=state,
            message=message,
            parameters=parameters or None,
        )

    def action(
        self, operator: str, action_type: str, run_id: str | None, details: str, station: str
    ) -> dict:
        return self.emit(
            "operator_action",
            "opvision-01",
            operator_id=operator,
            action_type=action_type,
            operation_run_id=run_id,
            station_id=station,
            details=details,
            confidence=0.9,
        )

    def start(
        self,
        item_id: str,
        stage: str,
        operator: str,
        *,
        previous_run_id: str | None = None,
        rework_reason: str | None = None,
    ) -> str:
        self._runs += 1
        run_id = f"RUN-{self.prefix}-{self._runs:03d}"
        self.emit(
            "operation_started",
            TERMINALS[stage],
            item_id=item_id,
            operation_run_id=run_id,
            operation_id=OPERATIONS[stage],
            line_id=LINE,
            station_id=STATIONS[stage],
            operator_id=operator,
            equipment_id=EQUIPMENT[stage],
            previous_run_id=previous_run_id,
            rework_reason=rework_reason,
            identification="reliable",
        )
        return run_id

    def finish(
        self, item_id: str, stage: str, run_id: str, minutes: float, *, delay_min: float = 0.2
    ) -> dict:
        return self.emit(
            "operation_finished",
            TERMINALS[stage],
            delay_min=delay_min,
            item_id=item_id,
            operation_run_id=run_id,
            outcome="completed",
            reported_duration={"value": minutes, "unit": "min", "meaning": "active_processing"},
        )

    def operate(
        self, item_id: str, stage: str, operator: str, minutes: float, **start_kwargs
    ) -> str:
        """Операция целиком: начало, работа станка и завершение."""

        run_id = self.start(item_id, stage, operator, **start_kwargs)
        self.machine(EQUIPMENT[stage], "running")
        self.advance(minutes)
        self.finish(item_id, stage, run_id, minutes)
        self.advance(2)
        return run_id

    def link(self, unit_id: str, component_id: str, run_id: str) -> None:
        self.emit(
            "component_linked",
            TERMINALS["assembly"],
            item_id=unit_id,
            component_item_id=component_id,
            operation_run_id=run_id,
            station_id=STATIONS["assembly"],
        )

    # --- действия людей и проверки ---------------------------------------------------

    def decide(
        self,
        item_id: str,
        defect_type: str,
        area: str | None,
        action: str,
        user_id: str,
        reason: str,
        cause_category: str | None = None,
    ) -> None:
        self.steps.append(
            {
                "kind": "decide",
                "at": self.clock.isoformat(),
                "nc_id": nc_id_for(item_id, defect_type, area),
                "action": action,
                "user_id": user_id,
                "reason": reason,
                "cause_category": cause_category,
            }
        )

    def tamper(self, event_id: str) -> None:
        self.steps.append({"kind": "tamper", "at": self.clock.isoformat(), "event_id": event_id})


def defect(
    defect_type: str, area: str, severity: str, description: str, component: str | None = None
) -> dict:
    result = {
        "defect_type": defect_type,
        "area": area,
        "severity": severity,
        "description": description,
    }
    if component:
        result["component_item_id"] = component
    return result


def standard_unit(
    b: Builder, n: str, *, mill_operator: str = "OP-101", weld_operator: str = "OP-102"
) -> dict:
    """Нормальное изготовление одного узла: корпус, фланец, сборка, финальный контроль."""

    body, flange, unit = f"B-{n}", f"F-{n}", f"U-{n}"
    order = f"WO-{n}"
    b.register(body, "BODY-K1", "manufactured", order)
    b.register(flange, "FLANGE-F2", "purchased", order)
    b.register(unit, "UNIT-U1", "manufactured", order)
    b.advance(5)
    b.inspect(body, "incoming")
    b.inspect(flange, "incoming")
    b.advance(10)
    mill = b.operate(body, "mill", mill_operator, 25)
    b.inspect(body, "mill", run_id=mill)
    b.advance(8)
    weld = b.operate(body, "weld", weld_operator, 20)
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
    return {"body": body, "flange": flange, "unit": unit, "runs": [mill, weld, assembly]}
