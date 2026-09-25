"""
Выгрузка истории в формате OCEL 2.0 (Object-Centric Event Log) для анализа процессов.

В OCEL событие связано сразу с несколькими объектами — изделием, выполнением операции,
оператором, оборудованием, участком. Ровно так устроена наша история, поэтому выгрузка
получается без потерь, и технолог открывает её в инструментах анализа процессов
(PM4Py, Celonis и другие) без нашей системы.
"""

from __future__ import annotations

from zero_defect.service import State

OBJECT_TYPES = {
    "item": ["item_type_id", "status"],
    "operation_run": ["operation_id", "status"],
    "operator": [],
    "equipment": [],
    "station": [],
    "nonconformance": ["defect_type", "status"],
}


def export(state: State) -> dict:
    history = state.history
    objects: dict[tuple[str, str], dict] = {}

    def obj(kind: str, object_id: str | None, **attributes) -> str | None:
        if not object_id:
            return None
        key = (kind, object_id)
        if key not in objects:
            objects[key] = {
                "id": f"{kind}:{object_id}",
                "type": kind,
                "attributes": [
                    {"name": name, "value": value, "time": "1970-01-01T00:00:00Z"}
                    for name, value in attributes.items()
                    if value is not None
                ],
                "relationships": [],
            }
        return objects[key]["id"]

    for item_id, item in history.items.items():
        obj("item", item_id, item_type_id=item.item_type_id, status=state.statuses.get(item_id))
        for component in item.components:
            objects[("item", item_id)]["relationships"].append(
                {"objectId": f"item:{component}", "qualifier": "contains"}
            )
    for run in history.runs.values():
        obj("operation_run", run.run_id, operation_id=run.operation_id, status=run.status)
    for card in state.cards.values():
        obj("nonconformance", card.nc_id, defect_type=card.defect_type, status=card.status)
        objects[("nonconformance", card.nc_id)]["relationships"].append(
            {"objectId": f"item:{card.item_id}", "qualifier": "concerns"}
        )

    events = []
    for event in history.events:
        relations = []
        for kind, object_id, qualifier in (
            ("item", event.item_id, "item"),
            ("item", event.component_item_id, "component"),
            ("operation_run", event.operation_run_id, "run"),
            ("operator", event.operator_id, "performed_by"),
            ("equipment", event.equipment_id, "equipment"),
            ("station", event.station_id, "station"),
        ):
            ref = obj(kind, object_id)
            if ref:
                relations.append({"objectId": ref, "qualifier": qualifier})
        attributes = [
            {"name": name, "value": value}
            for name, value in (
                ("source_id", event.source_id),
                ("inspection_result", event.inspection_result),
                ("machine_state", event.machine_state),
                ("action_type", event.action_type),
                ("received_at", event.received_at.isoformat()),
            )
            if value is not None
        ]
        events.append(
            {
                "id": event.event_id,
                "type": event.event_type,
                "time": event.occurred_at.isoformat(),
                "attributes": attributes,
                "relationships": relations,
            }
        )
    event_types = sorted({event["type"] for event in events})
    return {
        "objectTypes": [
            {"name": name, "attributes": [{"name": attr, "type": "string"} for attr in attrs]}
            for name, attrs in OBJECT_TYPES.items()
        ],
        "eventTypes": [
            {
                "name": name,
                "attributes": [
                    {"name": attr, "type": "string"}
                    for attr in (
                        "source_id",
                        "inspection_result",
                        "machine_state",
                        "action_type",
                        "received_at",
                    )
                ],
            }
            for name in event_types
        ],
        "objects": list(objects.values()),
        "events": events,
    }
