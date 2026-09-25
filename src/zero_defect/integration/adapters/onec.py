"""
Заготовка адаптера 1С:Предприятие через стандартный REST-интерфейс OData (платформа 8.3.5+).

1С — источник достоверных сведений о номенклатуре и заказах на производство, наша
система — об итогах контроля. Идентификаторы 1С — GUID (Ref_Key); внутренний код типа
изделия берётся из реквизита Code, а соответствие Ref_Key ↔ код хранится в таблице
сопоставления, чтобы итог уходил в 1С со ссылкой на её объект.

Имена объектов конфигурации (Catalog_Номенклатура, Document_ЗаказНаПроизводство,
Document_КонтрольКачества) — проектное предположение: в конкретной конфигурации они
могут называться иначе и настраиваются в OBJECTS без правки кода.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from zero_defect.config import AdapterSettings
from zero_defect.integration.model import (
    Ack,
    IntegrationError,
    PlannedItem,
    QualityResult,
    ReferenceData,
    WorkOrder,
)

ODATA_ROOT = "/odata/standard.odata"
OBJECTS = {
    "nomenclature": "Catalog_Номенклатура",
    "orders": "Document_ЗаказНаПроизводство",
    "result": "Document_КонтрольКачества",
}
VERDICTS = {"conforming": "Годно", "nonconforming": "Брак", "suspect": "НаПроверке"}


def url(entity: str, query: str = "$format=json") -> str:
    return f"{ODATA_ROOT}/{quote(entity)}?{query}"


def nomenclature_to_internal(payload: dict) -> tuple[ReferenceData, dict[str, str]]:
    """Справочник номенклатуры → типы изделий и сопоставление Ref_Key → код."""

    types, keys = {}, {}
    for row in payload.get("value", []):
        types[row["Code"].strip()] = row.get("Description", "")
        keys[row["Ref_Key"]] = row["Code"].strip()
    return ReferenceData(item_types=types), keys


def order_to_internal(row: dict, keys: dict[str, str]) -> WorkOrder:
    items = []
    for line in row.get("Продукция", []):
        type_id = keys.get(line["Номенклатура_Key"])
        if type_id is None:
            raise IntegrationError(
                f"в заказе {row['Number']} номенклатура {line['Номенклатура_Key']} не сопоставлена",
                retryable=False,
            )
        items.append(PlannedItem(line["СерийныйНомер"], type_id, "manufactured"))
    return WorkOrder(
        work_order_id=row["Number"].strip(),
        item_type_id=items[0].item_type_id if items else "",
        line_id=row.get("Линия"),
        items=tuple(items),
        due_date=row.get("ДатаПотребности"),
        external_id=row["Ref_Key"],
    )


def result_to_external(result: QualityResult, type_keys: dict[str, str]) -> dict:
    """Итог контроля → документ «Контроль качества». Ref_Key номенклатуры — из сопоставления."""

    reverse = {code: key for key, code in type_keys.items()}
    return {
        "Date": result.decided_at,
        "Номенклатура_Key": reverse.get(result.item_type_id or ""),
        "СерийныйНомер": result.item_id,
        "ЗаказНаПроизводство": result.work_order_id,
        "Результат": VERDICTS.get(result.verdict, "НаПроверке"),
        "ИдентификаторСообщения": result.message_id,
        "Несоответствия": [
            {"Код": nc["nc_id"], "ВидДефекта": nc["defect_type"], "Статус": nc["status"]}
            for nc in result.nonconformances
        ],
    }


class OneCAdapter:
    """Клиент OData 1С. Авторизация — базовая, учётная запись из переменных окружения."""

    name = "onec"

    def __init__(self, settings: AdapterSettings, client: httpx.Client | None = None) -> None:
        # trust_env=False: маршрут к внутренней системе задаётся конфигурацией, а не
        # системными переменными прокси — иначе запрос к MES в соседней стойке
        # внезапно уходил бы через прокси организации.
        self.client = client or httpx.Client(
            base_url=settings.base_url, timeout=settings.timeout_s, trust_env=False
        )
        self.type_keys: dict[str, str] = {}

    def fetch_reference(self) -> ReferenceData:
        response = self.client.get(
            url(OBJECTS["nomenclature"], "$format=json&$select=Ref_Key,Code,Description")
        )
        response.raise_for_status()
        reference, self.type_keys = nomenclature_to_internal(response.json())
        return reference

    def fetch_work_orders(self) -> list[WorkOrder]:
        if not self.type_keys:
            self.fetch_reference()
        response = self.client.get(url(OBJECTS["orders"], "$format=json&$filter=Posted eq true"))
        response.raise_for_status()
        return [order_to_internal(row, self.type_keys) for row in response.json().get("value", [])]

    def send_result(self, result: QualityResult) -> Ack:
        try:
            response = self.client.post(
                url(OBJECTS["result"]), json=result_to_external(result, self.type_keys)
            )
        except httpx.HTTPError as error:
            return Ack(
                result.message_id,
                False,
                error_code="connection_error",
                error_message=str(error),
                retryable=True,
            )
        if response.status_code >= 500:
            return Ack(
                result.message_id, False, error_code=f"http_{response.status_code}", retryable=True
            )
        if response.status_code >= 400:
            return Ack(
                result.message_id, False, error_code=f"http_{response.status_code}", retryable=False
            )
        return Ack(result.message_id, True, external_ref=response.json().get("Ref_Key"))
