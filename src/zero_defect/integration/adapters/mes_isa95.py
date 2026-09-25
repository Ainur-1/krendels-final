"""
Заготовка адаптера MES по модели ISA-95 (IEC 62264) в JSON-представлении по образцу B2MML.

Задание приходит как OperationsSchedule с запросами на сегменты и партиями материала,
итог уходит как OperationsPerformance с результатом испытаний по каждому серийному
номеру. Это образец формы B2MML, а не полное соответствие схемам XSD: конкретная MES
обычно поддерживает подмножество, и оно согласуется при подключении. Обмен с MES
позволяет не вводить повторно руками то, что система уже знает.
"""

from __future__ import annotations

from zero_defect.integration.model import PlannedItem, QualityResult, WorkOrder

RESULTS = {"conforming": "Pass", "nonconforming": "Fail", "suspect": "Pending"}


def schedule_to_internal(schedule: dict) -> list[WorkOrder]:
    orders = []
    for request in schedule["OperationsSchedule"].get("OperationsRequest", []):
        items = []
        for segment in request.get("SegmentRequirement", []):
            for material in segment.get("MaterialRequirement", []):
                for lot in material.get("MaterialLotID", []):
                    origin = (
                        "purchased" if material.get("MaterialUse") == "Consumed" else "manufactured"
                    )
                    items.append(
                        PlannedItem(
                            lot,
                            material["MaterialDefinitionID"],
                            origin,
                            material.get("AssemblyOf"),
                        )
                    )
        orders.append(
            WorkOrder(
                work_order_id=request["ID"],
                item_type_id=request.get("ProductID", ""),
                line_id=request.get("HierarchyScope", {}).get("EquipmentID"),
                items=tuple(items),
                due_date=request.get("EndTime"),
                external_id=request["ID"],
            )
        )
    return orders


def result_to_performance(result: QualityResult) -> dict:
    return {
        "OperationsPerformance": {
            "ID": result.message_id,
            "OperationsType": "Quality",
            "PublishedDate": result.decided_at,
            "OperationsResponse": [
                {
                    "ID": f"{result.work_order_id}-{result.item_id}",
                    "OperationsRequestID": result.work_order_id,
                    "SegmentResponse": [
                        {
                            "ID": "QC-FINAL",
                            "ProcessSegmentID": "QualityInspection",
                            "MaterialActual": [
                                {
                                    "MaterialDefinitionID": result.item_type_id,
                                    "MaterialLotID": result.item_id,
                                    "TestResult": [
                                        {
                                            "ID": nc["nc_id"],
                                            "Description": nc["defect_type"],
                                            "Result": nc["status"],
                                        }
                                        for nc in result.nonconformances
                                    ],
                                    "Disposition": RESULTS.get(result.verdict, "Pending"),
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }
