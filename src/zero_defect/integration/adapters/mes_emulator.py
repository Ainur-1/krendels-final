"""
Адаптер эмулятора MES: единственный двусторонний обмен, реализованный в MVP целиком.

Получает задания и справочники, отправляет итоги контроля и разбирает ответ. Коды
ответа разделены на две группы: 5xx и обрыв связи — временные, сообщение останется в
очереди и уйдёт позже; 4xx — ошибка содержания, повтор её не исправит, и сообщение
уходит в список недоставленных с причиной.
"""

from __future__ import annotations

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

VERDICTS = {"conforming": "OK", "nonconforming": "NOK", "suspect": "HOLD", "not_assessable": "HOLD"}
ORIGINS = {"make": "manufactured", "buy": "purchased"}


def order_to_internal(order: dict) -> WorkOrder:
    items = []
    for entry in order.get("serials", []):
        items.append(PlannedItem(entry["serial"], entry["code"], "manufactured"))
        for part in entry.get("bom", []):
            items.append(
                PlannedItem(
                    part["serial"],
                    part["code"],
                    ORIGINS.get(part.get("source"), "manufactured"),
                    entry["serial"],
                )
            )
    return WorkOrder(
        work_order_id=order["orderNo"],
        item_type_id=order["product"]["code"],
        line_id=order.get("line"),
        items=tuple(items),
        due_date=order.get("due"),
        external_id=order["orderNo"],
    )


def result_to_external(result: QualityResult) -> dict:
    return {
        "msgId": result.message_id,
        "orderNo": result.work_order_id,
        "serial": result.item_id,
        "product": result.item_type_id,
        "verdict": VERDICTS.get(result.verdict, "HOLD"),
        "issues": [
            {
                "ref": nc["nc_id"],
                "code": nc["defect_type"],
                "state": nc["status"],
                "cause": nc.get("cause"),
            }
            for nc in result.nonconformances
        ],
        "ts": result.decided_at,
    }


class MesEmulatorAdapter:
    """HTTP-клиент эмулятора. Клиент можно подставить — так его гоняют тесты."""

    name = "mes_emulator"

    def __init__(self, settings: AdapterSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        # trust_env=False: маршрут к внутренней системе задаётся конфигурацией, а не
        # системными переменными прокси — иначе запрос к MES в соседней стойке
        # внезапно уходил бы через прокси организации.
        self.client = client or httpx.Client(
            base_url=settings.base_url, timeout=settings.timeout_s, trust_env=False
        )

    def _get(self, path: str) -> dict:
        try:
            response = self.client.get(path)
        except httpx.HTTPError as error:
            raise IntegrationError(f"нет связи с эмулятором: {error}") from error
        if response.status_code >= 500:
            raise IntegrationError(f"эмулятор временно недоступен: {response.status_code}")
        if response.status_code >= 400:
            raise IntegrationError(
                f"эмулятор отклонил запрос: {response.status_code}", retryable=False
            )
        return response.json()

    def fetch_work_orders(self) -> list[WorkOrder]:
        return [order_to_internal(order) for order in self._get("/api/v1/orders")["orders"]]

    def fetch_reference(self) -> ReferenceData:
        raw = self._get("/api/v1/catalog")
        return ReferenceData(
            item_types={item["code"]: item["title"] for item in raw.get("products", [])},
            defect_types={item["code"]: item["title"] for item in raw.get("defectCodes", [])},
            stations={item["code"]: item["title"] for item in raw.get("workCenters", [])},
        )

    def send_result(self, result: QualityResult) -> Ack:
        try:
            response = self.client.post("/api/v1/quality-reports", json=result_to_external(result))
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
        body = response.json()
        if response.status_code >= 400:
            return Ack(
                result.message_id,
                False,
                error_code=body.get("error", f"http_{response.status_code}"),
                error_message="внешняя система отклонила содержание сообщения",
                retryable=False,
            )
        return Ack(result.message_id, True, external_ref=body.get("ref"))
