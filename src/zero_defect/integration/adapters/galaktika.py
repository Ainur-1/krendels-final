"""
Заготовка адаптера Галактика ERP через файловый XML-обмен.

Публичного REST API у Галактики ERP нет; штатные механизмы обмена — XML-файлы, прямой
доступ к данным и API-функции. Для закрытого контура выбран файловый обмен через
каталоги: он не требует сетевого доступа из ERP к нашей системе и легко проверяется.
Галактика — источник достоверных сведений о номенклатуре и заказах, мы — об итогах
контроля. Структура XML — проектное предположение, согласуется с интегратором ERP.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from zero_defect.integration.model import (
    Ack,
    IntegrationError,
    PlannedItem,
    QualityResult,
    ReferenceData,
    WorkOrder,
)

VERDICTS = {"conforming": "ACCEPT", "nonconforming": "REJECT", "suspect": "HOLD"}


def parse_nomenclature(xml_text: str) -> ReferenceData:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise IntegrationError(f"XML справочника повреждён: {error}", retryable=False) from error
    return ReferenceData(
        item_types={node.get("code"): node.get("name", "") for node in root.iter("Nomenclature")}
    )


def parse_orders(xml_text: str) -> list[WorkOrder]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as error:
        raise IntegrationError(f"XML заказов повреждён: {error}", retryable=False) from error
    orders = []
    for node in root.iter("ProductionOrder"):
        items = tuple(
            PlannedItem(
                unit.get("serial"),
                unit.get("code"),
                unit.get("origin", "manufactured"),
                unit.get("parent"),
            )
            for unit in node.iter("Unit")
        )
        orders.append(
            WorkOrder(
                work_order_id=node.get("number"),
                item_type_id=node.get("product"),
                line_id=node.get("line"),
                items=items,
                due_date=node.get("due"),
                external_id=node.get("nrec"),
            )
        )
    return orders


def result_to_xml(result: QualityResult) -> str:
    root = ET.Element("QualityResult", {"messageId": result.message_id, "version": "1"})
    ET.SubElement(
        root,
        "Item",
        {
            "serial": result.item_id,
            "code": result.item_type_id or "",
            "order": result.work_order_id or "",
            "verdict": VERDICTS.get(result.verdict, "HOLD"),
            "decidedAt": result.decided_at or "",
        },
    )
    issues = ET.SubElement(root, "Nonconformances")
    for nc in result.nonconformances:
        ET.SubElement(
            issues,
            "Nonconformance",
            {"id": nc["nc_id"], "defect": nc["defect_type"], "status": nc["status"]},
        )
    return ET.tostring(root, encoding="unicode")


class GalaktikaFileAdapter:
    """Обмен через каталоги: inbox — от ERP к нам, outbox — от нас к ERP."""

    name = "galaktika"

    def __init__(self, exchange_dir: Path) -> None:
        self.inbox = exchange_dir / "inbox"
        self.outbox = exchange_dir / "outbox"

    def fetch_reference(self) -> ReferenceData:
        path = self.inbox / "nomenclature.xml"
        return (
            parse_nomenclature(path.read_text(encoding="utf-8"))
            if path.exists()
            else ReferenceData()
        )

    def fetch_work_orders(self) -> list[WorkOrder]:
        path = self.inbox / "orders.xml"
        return parse_orders(path.read_text(encoding="utf-8")) if path.exists() else []

    def send_result(self, result: QualityResult) -> Ack:
        self.outbox.mkdir(parents=True, exist_ok=True)
        target = self.outbox / f"{result.message_id}.xml"
        if target.exists():
            return Ack(result.message_id, True, external_ref=target.name)
        # Сначала временный файл, затем переименование: ERP никогда не увидит
        # недописанный файл, даже если запись оборвётся на середине.
        partial = target.with_suffix(".part")
        partial.write_text(result_to_xml(result), encoding="utf-8")
        partial.rename(target)
        return Ack(result.message_id, True, external_ref=target.name)
