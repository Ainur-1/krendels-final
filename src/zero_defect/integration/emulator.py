"""
Эмулятор внешней системы уровня MES/ERP для двустороннего обмена в MVP.

Говорит на своём формате, намеренно не похожем на внутренний: заказы называются
orderNo, изделия — serial, итог — verdict OK/NOK/HOLD. Так проверяется, что перевод
форматов действительно живёт в адаптере. Умеет изображать сбои: следующие N запросов
отвечают 503, и видно, что исходящая очередь их переживает.

Запуск отдельным процессом: uv run uvicorn zero_defect.integration.emulator:app --port 8100
"""

from __future__ import annotations

import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="MES emulator", version="1.0")

_lock = threading.Lock()
_faults = {"fail_next": 0, "status": 503}
_received: dict[str, dict] = {}

ORDERS = [
    {
        "orderNo": "WO-9001",
        "line": "L1",
        "due": "2026-09-27",
        "product": {"code": "UNIT-U1"},
        "serials": [
            {
                "serial": "U-9001",
                "code": "UNIT-U1",
                "bom": [
                    {"serial": "B-9001", "code": "BODY-K1", "source": "make"},
                    {"serial": "F-9001", "code": "FLANGE-F2", "source": "buy"},
                ],
            }
        ],
    },
    {
        "orderNo": "WO-9002",
        "line": "L1",
        "due": "2026-09-28",
        "product": {"code": "UNIT-U1"},
        "serials": [
            {
                "serial": "U-9002",
                "code": "UNIT-U1",
                "bom": [
                    {"serial": "B-9002", "code": "BODY-K1", "source": "make"},
                    {"serial": "F-9002", "code": "FLANGE-F2", "source": "buy"},
                ],
            }
        ],
    },
]

CATALOG = {
    "products": [
        {"code": "UNIT-U1", "title": "Узел крепления в сборе"},
        {"code": "BODY-K1", "title": "Корпус"},
        {"code": "FLANGE-F2", "title": "Фланец (покупной)"},
    ],
    "defectCodes": [
        {"code": "INCLUSION", "title": "Включение в материале"},
        {"code": "POROSITY", "title": "Пористость шва"},
        {"code": "LACK_OF_FUSION", "title": "Непровар"},
        {"code": "CRACK", "title": "Трещина"},
        {"code": "BURR", "title": "Заусенец"},
        {"code": "SCRATCH", "title": "Риска, царапина"},
    ],
    "workCenters": [
        {"code": "ST-INC", "title": "Входной контроль"},
        {"code": "ST-MILL", "title": "Механообработка"},
        {"code": "ST-WELD", "title": "Сварка"},
        {"code": "ST-ASM", "title": "Сборка"},
        {"code": "ST-QC", "title": "ОТК, финальный контроль"},
    ],
}


class Issue(BaseModel):
    ref: str
    code: str
    state: str
    cause: str | None = None


class QualityReport(BaseModel):
    msgId: str
    orderNo: str | None = None
    serial: str
    product: str | None = None
    verdict: str
    issues: list[Issue] = []
    ts: str | None = None


class Faults(BaseModel):
    fail_next: int = 0
    status: int = 503


def _maybe_fail() -> None:
    with _lock:
        if _faults["fail_next"] > 0:
            _faults["fail_next"] -= 1
            raise HTTPException(status_code=_faults["status"], detail="temporarily_unavailable")


@app.get("/api/v1/orders")
def orders() -> dict:
    _maybe_fail()
    return {"orders": ORDERS}


@app.get("/api/v1/catalog")
def catalog() -> dict:
    _maybe_fail()
    return CATALOG


@app.post("/api/v1/quality-reports")
def quality_report(report: QualityReport) -> JSONResponse:
    _maybe_fail()
    known_serials = {
        serial
        for order in ORDERS
        for entry in order["serials"]
        for serial in [entry["serial"], *(part["serial"] for part in entry["bom"])]
    }
    if report.verdict not in {"OK", "NOK", "HOLD"}:
        return JSONResponse({"error": "bad_verdict", "msgId": report.msgId}, status_code=422)
    if report.serial not in known_serials:
        return JSONResponse({"error": "unknown_serial", "msgId": report.msgId}, status_code=422)
    with _lock:
        existing = _received.get(report.msgId)
        if existing is not None:
            return JSONResponse(
                {"msgId": report.msgId, "status": "duplicate", "ref": existing["ref"]}
            )
        ref = f"QR-{uuid.uuid4().hex[:8].upper()}"
        _received[report.msgId] = {"ref": ref, "report": report.model_dump()}
    return JSONResponse({"msgId": report.msgId, "status": "accepted", "ref": ref}, status_code=202)


@app.get("/api/v1/quality-reports")
def received() -> dict:
    return {"reports": list(_received.values())}


@app.post("/admin/faults")
def set_faults(faults: Faults) -> dict:
    with _lock:
        _faults.update(faults.model_dump())
    return dict(_faults)


def reset() -> None:
    """Очищает состояние эмулятора; нужен тестам."""

    with _lock:
        _received.clear()
        _faults.update({"fail_next": 0, "status": 503})
