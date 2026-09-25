"""
Администрирование: база данных, права ролей, справочник дефектов и оборудование.

Просмотр базы — только чтение и только то, что безопасно показать: у журнала — открытые
заголовки записей без шифротекста, у пользователей — признак заданного пароля без
хеша. Всё, что меняет данные, делается через ядро с аудитом, а не отсюда.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import func, select

from zero_defect.lines.catalog import DEFECTS, title_of
from zero_defect.lines.store import LineStore
from zero_defect.security.auth import PERMISSIONS, Principal
from zero_defect.service import QualitySystem
from zero_defect.storage.database import metadata

# Что означает каждое право — для матрицы «что могут делать пользователи».
PERMISSION_TITLES = {
    "read": "видеть линию, изделия, несоответствия, показатели",
    "decide": "подтверждать, отклонять, закрывать несоответствия",
    "set_cause": "устанавливать причину подтверждённого несоответствия",
    "export": "выгружать историю (OCEL 2.0)",
    "line_manage": "создавать и менять линии, параметры экономики",
    "emulate": "управлять эмуляцией и прогонами в пульте эмулятора",
    "integrate": "запускать обмен с внешними системами",
    "admin": "целостность журнала, ключи, аудит, база данных, пользователи",
    "view_as": "смотреть интерфейс глазами другой роли",
    "ingest": "передавать события в систему",
}

# Столбцы, которые в просмотре не показываются никогда: секреты и шифротекст.
HIDDEN = {
    "users": {"password_hash"},
    "ledger": {"nonce", "ciphertext", "prev_hash", "record_hash", "signature"},
    "outbox": {"payload"},
    "lines": {"config"},
}


def register(app: FastAPI, system: QualitySystem, lines: LineStore, principal) -> None:
    def admin(user: Principal = Depends(principal)) -> Principal:
        system.security.authorize(user, "admin", "admin_read")
        return user

    @app.get("/api/admin/db")
    def database(table: str | None = None, limit: int = 30, _: Principal = Depends(admin)) -> dict:
        tables = metadata.tables
        summary = []
        with system.database.engine.connect() as conn:
            for name, model in tables.items():
                count = conn.execute(select(func.count()).select_from(model)).scalar_one()
                summary.append(
                    {
                        "table": name,
                        "rows": count,
                        "columns": [
                            c.name for c in model.columns if c.name not in HIDDEN.get(name, set())
                        ],
                        "hidden": sorted(HIDDEN.get(name, set())),
                    }
                )
            rows = []
            if table:
                model = tables.get(table)
                if model is None:
                    raise HTTPException(404, f"таблицы {table} нет")
                visible = [c for c in model.columns if c.name not in HIDDEN.get(table, set())]
                order = (
                    model.c.seq.desc() if "seq" in model.c else list(model.primary_key.columns)[0]
                )
                for row in conn.execute(
                    select(*visible).order_by(order).limit(max(1, min(limit, 200)))
                ).mappings():
                    rows.append(
                        {
                            key: (
                                value
                                if isinstance(value, (int, float, str, bool)) or value is None
                                else str(value)
                            )
                            for key, value in row.items()
                        }
                    )
                if table == "users":
                    for row in rows:
                        row["has_password"] = system.users.has_password(row["user_id"])
        return {"dialect": system.database.dialect, "tables": summary, "table": table, "rows": rows}

    @app.get("/api/admin/roles")
    def roles(_: Principal = Depends(admin)) -> dict:
        roles_order = ["controller", "master", "technologist", "manager", "admin", "edge"]
        return {
            "permissions": [
                {"permission": key, "title": title} for key, title in PERMISSION_TITLES.items()
            ],
            "roles": {role: sorted(PERMISSIONS.get(role, ())) for role in roles_order},
            "users": [
                {
                    "user_id": u.user_id,
                    "name": u.name,
                    "role": u.role,
                    "has_password": system.users.has_password(u.user_id),
                }
                for u in system.security.users.values()
            ],
        }

    @app.get("/api/admin/defects")
    def defects(_: Principal = Depends(admin)) -> list[dict]:
        """Справочник дефектов: название, где возникает по конфигурации, сколько найдено."""

        state = system.snapshot()
        configured: dict[str, list[str]] = defaultdict(list)
        for config in lines.all():
            for node in config.nodes:
                for code in node.defect_types:
                    configured[code].append(f"{config.line_id} · {node.title}")
        found = Counter(card.defect_type for card in state.cards.values())
        confirmed = Counter(
            card.defect_type
            for card in state.cards.values()
            if card.status in ("confirmed", "closed")
        )
        codes = sorted(set(DEFECTS) | set(configured) | set(found))
        return [
            {
                "code": code,
                "title": title_of(code),
                "method": DEFECTS.get(code, {}).get("method", "—"),
                "where": configured.get(code, []),
                "found": found.get(code, 0),
                "confirmed": confirmed.get(code, 0),
            }
            for code in codes
        ]

    @app.get("/api/admin/equipment")
    def equipment(_: Principal = Depends(admin)) -> list[dict]:
        """Оборудование линий: на каком этапе стоит, кто работает, последнее состояние."""

        state = system.snapshot()
        rows = []
        for config in lines.all():
            for node in config.nodes:
                if node.kind != "operation":
                    continue
                events = state.history.machine_events.get(node.equipment_id or "", [])
                rows.append(
                    {
                        "line_id": config.line_id,
                        "line_title": config.title,
                        "node_id": node.node_id,
                        "stage": node.title,
                        "equipment_id": node.equipment_id,
                        "operators": node.operators,
                        "duration_min": round(node.duration_s / 60, 1),
                        "state": events[-1].machine_state if events else None,
                        "deviations": sum(
                            1
                            for event in events
                            if event.machine_state in ("warning", "deviation", "stopped")
                        ),
                    }
                )
        return rows
