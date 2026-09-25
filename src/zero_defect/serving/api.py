"""
HTTP-сервис: API ядра и интерфейс.

Каждый эндпоинт, кроме проверки здоровья, получает пользователя через шину безопасности
и проверяет право на действие там же. Сами эндпоинты — тонкая обёртка: разбор запроса,
вызов ядра, представление ответа.

Запуск: uv run uvicorn zero_defect.serving.api:create_app --factory --port 8000

Приложение собирается фабрикой, а не при импорте модуля: импорт не должен открывать
журнал и проигрывать демонстрационный набор — тесты собирают своё приложение со своими
временными каталогами.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from zero_defect import __version__
from zero_defect.analysis import ocel
from zero_defect.config import DATA_DIR, SCHEMAS_DIR, STATIC_DIR, Settings, load_settings
from zero_defect.integration.hub import IntegrationHub, Outbox
from zero_defect.integration.model import ExternalSystem
from zero_defect.integration.registry import build_adapters
from zero_defect.ledger.crypto import PROFILES
from zero_defect.security.auth import PERMISSIONS, AuthError, Principal, issue_token
from zero_defect.service import DecisionError, QualitySystem
from zero_defect.serving import payloads
from zero_defect.simulation.replay import load_steps, replay


class LoginRequest(BaseModel):
    user_id: str


class DecisionRequest(BaseModel):
    action: str
    reason: str
    cause_category: str | None = None


class RotateRequest(BaseModel):
    profile_id: str | None = None


def create_app(
    settings: Settings | None = None,
    adapters: dict[str, ExternalSystem] | None = None,
    load_demo: bool | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    system = QualitySystem(settings)
    outbox = Outbox(settings.storage_path.with_name("integration.sqlite3"))
    hub = IntegrationHub(
        system, adapters if adapters is not None else build_adapters(settings), outbox
    )
    demo_path = DATA_DIR / "demo" / "input.jsonl"
    if (load_demo if load_demo is not None else settings.demo) and not system.ingest_summary()[
        "accepted"
    ]:
        # Демонстрационный набор проигрывается один раз, в пустой журнал. Повторный
        # запуск его не дублирует: журнал уже не пуст.
        replay(system, load_steps(demo_path))

    app = FastAPI(
        title="Zero Defect",
        version=__version__,
        description="Прослеживаемость изделия и разбор несоответствий на закрытом производстве.",
    )
    app.state.system = system
    app.state.hub = hub

    @app.exception_handler(AuthError)
    def auth_error(_: Request, error: AuthError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    def principal(authorization: str | None = Header(default=None)) -> Principal:
        token = authorization.removeprefix("Bearer ").strip() if authorization else None
        return system.security.from_token(token)

    def reader(user: Principal = Depends(principal)) -> Principal:
        system.security.authorize(user, "read", "read")
        return user

    # --- служебное ------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "records": system.ledger.count()}

    @app.get("/api/users")
    def users() -> list[dict]:
        if not settings.demo:
            raise HTTPException(404)
        return [
            {"user_id": user.user_id, "name": user.name, "role": user.role}
            for user in system.security.users.values()
        ]

    @app.post("/api/auth/demo-login")
    def demo_login(request: LoginRequest) -> dict:
        if not settings.demo:
            raise HTTPException(404, "вход без токена доступен только в демонстрационном режиме")
        user = system.security.user(request.user_id)
        system.audit("login", user, {"mode": "demo"})
        return {
            "token": issue_token(system.keyring, user, settings.token_ttl_s),
            "user": user.__dict__,
        }

    @app.get("/api/me")
    def me(user: Principal = Depends(principal)) -> dict:
        return {**user.__dict__, "permissions": sorted(_permissions(user))}

    # --- приём событий --------------------------------------------------------------

    @app.post("/api/events")
    async def post_events(
        request: Request,
        authorization: str | None = Header(default=None),
        x_source_id: str | None = Header(default=None),
        x_signature: str | None = Header(default=None),
    ) -> dict:
        body = await request.body()
        if x_signature:
            sender = system.security.from_source(x_source_id, body, x_signature)
        else:
            token = authorization.removeprefix("Bearer ").strip() if authorization else None
            sender = system.security.from_token(token)
        system.security.authorize(sender, "ingest", "ingest")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as error:
            raise HTTPException(400, f"тело запроса — не JSON: {error}") from error
        messages = data.get("events", data) if isinstance(data, dict) else data
        if isinstance(messages, dict):
            messages = [messages]
        results = system.ingest(messages, received_at=datetime.now(UTC))
        return {"results": [result.as_dict() for result in results]}

    # --- представления --------------------------------------------------------------

    @app.get("/api/line")
    def line(_: Principal = Depends(reader)) -> dict:
        return payloads.line_view(system)

    @app.get("/api/items")
    def items(status: str | None = None, _: Principal = Depends(reader)) -> list[dict]:
        state = system.state()
        rows = []
        for item_id, item in sorted(state.history.items.items()):
            if status and state.statuses.get(item_id) != status:
                continue
            rows.append(
                {
                    "item_id": item_id,
                    "item_type_id": item.item_type_id,
                    "status": state.statuses.get(item_id),
                    "parent_id": item.parent_id,
                    "components": item.components,
                    "work_order_id": item.work_order_id,
                    "runs": len(item.run_ids),
                    "observations": len(item.observations),
                }
            )
        return rows

    @app.get("/api/items/{item_id}")
    def item(item_id: str, _: Principal = Depends(reader)) -> dict:
        view = payloads.item_view(item_id, system)
        if view is None:
            raise HTTPException(404, f"изделие {item_id} не найдено")
        return view

    @app.get("/api/nonconformances")
    def nonconformances(status: str | None = None, _: Principal = Depends(reader)) -> list[dict]:
        cards = system.state().cards.values()
        return [
            payloads.nc_summary(card)
            for card in sorted(cards, key=lambda card: card.first_detected_at, reverse=True)
            if status is None or card.status == status
        ]

    @app.get("/api/nonconformances/{nc_id}")
    def nonconformance(nc_id: str, user: Principal = Depends(reader)) -> dict:
        card = system.state().cards.get(nc_id)
        if card is None:
            raise HTTPException(404, f"несоответствие {nc_id} не найдено")
        return payloads.nc_card(card, system, user)

    @app.post("/api/nonconformances/{nc_id}/decisions")
    def decide(nc_id: str, request: DecisionRequest, user: Principal = Depends(principal)) -> dict:
        try:
            decision = system.decide(
                user, nc_id, request.action, request.reason, request.cause_category
            )
        except DecisionError as error:
            raise HTTPException(409, str(error)) from error
        card = system.state().cards[nc_id]
        return {"decision": decision.as_dict(), "card": payloads.nc_card(card, system, user)}

    @app.get("/api/metrics")
    def metrics(_: Principal = Depends(reader)) -> dict:
        return {**system.state().metrics, "ingest": system.ingest_summary()}

    @app.get("/api/quarantine")
    def quarantine(_: Principal = Depends(reader)) -> list[dict]:
        return system.state().quarantine

    # --- защита ---------------------------------------------------------------------

    @app.get("/api/integrity")
    def integrity(user: Principal = Depends(principal)) -> dict:
        system.security.authorize(user, "admin", "integrity_check")
        report = system.verify_integrity().as_dict()
        system.audit(
            "integrity_result", user, {"ok": report["ok"], "problems": len(report["problems"])}
        )
        return report

    @app.get("/api/audit")
    def audit(user: Principal = Depends(principal)) -> list[dict]:
        system.security.authorize(user, "admin", "audit_read")
        return list(reversed(system.critical_actions))

    @app.get("/api/keys")
    def keys(user: Principal = Depends(principal)) -> dict:
        system.security.authorize(user, "admin", "keys_read")
        return {
            "active": system.keyring.active().key_id,
            "profiles": {name: profile.mechanism for name, profile in PROFILES.items()},
            "keys": [
                {
                    "key_id": info.key_id,
                    "key_version": info.key_version,
                    "profile_id": info.profile_id,
                    "status": info.status,
                    "created_at": info.created_at,
                    "secret_available": system.keyring.has_secret(info.key_id),
                }
                for info in system.keyring.keys()
            ],
        }

    @app.post("/api/keys/rotate")
    def rotate(request: RotateRequest, user: Principal = Depends(principal)) -> dict:
        system.security.authorize(user, "admin", "key_rotation", {"profile_id": request.profile_id})
        try:
            return system.rotate_key(request.profile_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    # --- интеграции -----------------------------------------------------------------

    @app.get("/api/integration")
    def integration(_: Principal = Depends(reader)) -> dict:
        return {
            "adapters": sorted(hub.adapters),
            "last_errors": hub.last_errors,
            "outbox": outbox.rows(),
            "id_map": outbox.id_map(),
            "exchanges": list(reversed(system.exchanges))[:50],
        }

    @app.post("/api/integration/sync")
    def sync(user: Principal = Depends(principal)) -> dict:
        system.security.authorize(user, "integrate", "integration_sync")
        return hub.sync()

    # --- выгрузки -------------------------------------------------------------------

    @app.get("/api/export/ocel")
    def export_ocel(user: Principal = Depends(principal)) -> JSONResponse:
        system.security.authorize(user, "export", "export_ocel")
        return JSONResponse(
            ocel.export(system.state()),
            headers={"Content-Disposition": 'attachment; filename="zero-defect.ocel.json"'},
        )

    @app.get("/api/contracts")
    def contracts(_: Principal = Depends(reader)) -> dict:
        return {
            "versions": sorted(path.name for path in SCHEMAS_DIR.glob("*.schema.json")),
        }

    @app.get("/api/contracts/{name}")
    def contract(name: str, _: Principal = Depends(reader)) -> FileResponse:
        path = SCHEMAS_DIR / Path(name).name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="application/json")

    @app.get("/api/ops/metrics")
    def ops_metrics(_: Principal = Depends(reader)) -> dict:
        return system.telemetry.snapshot()

    # --- интерфейс ------------------------------------------------------------------

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        page = STATIC_DIR / "index.html"
        return HTMLResponse(page.read_text(encoding="utf-8"))

    return app


def _permissions(user: Principal) -> set[str]:
    return set(PERMISSIONS.get(user.role, ()))
