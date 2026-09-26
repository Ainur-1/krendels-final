"""
Роли в базе: права и экран, который роль видит.

Встроенные роли постановки — начальное наполнение из security/auth.py. Администратор
меняет их права и заводит новые роли; новая роль выбирает один из готовых экранов
(контролёр, мастер, технолог, руководитель, администратор) и набор прав из известных
системе. Справочник обновляет PERMISSIONS и ROLE_META на месте, поэтому проверка прав в
шине безопасности не меняется.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime

from sqlalchemy import insert, select, update

from zero_defect.security.auth import PERMISSIONS, ROLE_META, SCREENS
from zero_defect.storage.database import Database
from zero_defect.storage.database import catalog as catalog_table

ROLE_CODE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class RoleError(ValueError):
    """Роль описана некорректно."""


class RoleStore:
    """Роли и их права в базе поверх встроенного наполнения."""

    def __init__(self, database: Database, known_permissions: set[str]) -> None:
        self.database = database
        self.known = known_permissions
        self._lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        with self.database.engine.connect() as conn:
            rows = conn.execute(
                select(catalog_table.c.code, catalog_table.c.config).where(
                    catalog_table.c.kind == "role"
                )
            ).all()
        with self._lock:
            for code, raw in rows:
                spec = json.loads(raw)
                PERMISSIONS[code] = frozenset(spec["permissions"])
                ROLE_META[code] = {"title": spec["title"], "screen": spec["screen"]}

    def all(self) -> dict[str, dict]:
        return {
            code: {
                **ROLE_META.get(code, {"title": code, "screen": "none"}),
                "permissions": sorted(perms),
            }
            for code, perms in PERMISSIONS.items()
            if code != "edge"
        }

    def save(self, code: str, spec: dict, author: str, create: bool) -> dict:
        title = str(spec.get("title", "")).strip()
        screen = str(spec.get("screen", ""))
        permissions = sorted({str(p) for p in spec.get("permissions", [])})
        problems = []
        if not ROLE_CODE.match(code):
            problems.append("код роли: строчная латиница, цифры и подчёркивание")
        if code == "edge":
            problems.append("служебная роль источников событий не меняется")
        if create and code in PERMISSIONS:
            problems.append(f"роль {code} уже есть")
        if not create and code not in PERMISSIONS:
            problems.append(f"роли {code} нет")
        if not title:
            problems.append("не указано название роли")
        if screen not in SCREENS:
            problems.append("экран роли должен быть одним из готовых")
        unknown = [p for p in permissions if p not in self.known]
        if unknown:
            problems.append(f"неизвестные права: {', '.join(unknown)}")
        # Администратор не может отнять у своей роли администрирование: иначе систему
        # некому будет настраивать.
        if code == "admin" and "admin" not in permissions:
            problems.append("у роли администратора нельзя отнять администрирование")
        if problems:
            raise RoleError("; ".join(problems))
        value = {"title": title, "screen": screen, "permissions": permissions}
        row = {
            "config": json.dumps(value, ensure_ascii=False),
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": author,
        }
        with self.database.engine.begin() as conn:
            exists = conn.execute(
                select(catalog_table.c.code).where(
                    catalog_table.c.kind == "role", catalog_table.c.code == code
                )
            ).first()
            if exists:
                conn.execute(
                    update(catalog_table)
                    .where(catalog_table.c.kind == "role", catalog_table.c.code == code)
                    .values(**row)
                )
            else:
                conn.execute(insert(catalog_table).values(kind="role", code=code, **row))
        with self._lock:
            PERMISSIONS[code] = frozenset(permissions)
            ROLE_META[code] = {"title": title, "screen": screen}
        return {**value, "code": code}
