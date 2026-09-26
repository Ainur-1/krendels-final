"""
Пользователи, роли, токены и подпись пакетов от источников.

Всё, что приходит в систему — запрос пользователя или пакет событий от edge-агента, —
проходит через одну точку: SecurityBus. Она проверяет подлинность, права и записывает
критические действия в журнал. Отдельные эндпоинты сами ничего не проверяют, поэтому
новый эндпоинт не может «забыть» проверку прав — он её просто не получит без шины.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from zero_defect.ledger.crypto import Keyring

# Права ролей. Роли — из постановки; служебная роль edge нужна источникам событий. Это
# начальное наполнение: администратор меняет права и заводит новые роли, и справочник
# ролей в базе (security/roles.py) обновляет этот словарь на месте.
PERMISSIONS: dict[str, frozenset[str]] = {
    "controller": frozenset({"read", "decide"}),
    "master": frozenset({"read", "emulate"}),
    # Технолог заводит конкретный станок по типу, который описал администратор.
    "technologist": frozenset({"read", "set_cause", "export", "equipment_manage"}),
    # Руководитель производства — главная роль линии: создаёт линии и видит их экономику.
    "manager": frozenset({"read", "export", "line_manage", "emulate"}),
    "admin": frozenset(
        {
            "read",
            "admin",
            "ingest",
            "export",
            "integrate",
            "line_manage",
            "emulate",
            "view_as",
            "equipment_manage",
        }
    ),
    "edge": frozenset({"ingest"}),
}

# Название роли и экран, который она видит. Новая роль выбирает один из этих экранов.
ROLE_META: dict[str, dict[str, str]] = {
    "controller": {"title": "Контролёр ОТК", "screen": "controller"},
    "master": {"title": "Мастер участка", "screen": "master"},
    "technologist": {"title": "Технолог", "screen": "technologist"},
    "manager": {"title": "Руководитель производства", "screen": "manager"},
    "admin": {"title": "Администратор", "screen": "admin"},
    "edge": {"title": "Источник событий", "screen": "none"},
}
SCREENS = ("controller", "master", "technologist", "manager", "admin")

# Действия, которые записываются в журнал критических действий. Чтение не пишется:
# журнал, в котором тонут решения, никто не читает.
CRITICAL = frozenset(
    {
        "decide",
        "set_cause",
        "admin",
        "integrate",
        "login",
        "denied",
        "line_manage",
        "emulate",
        "equipment_manage",
    }
)


class AuthError(Exception):
    """Нет подлинности или нет прав. status — HTTP-код для ответа."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    """Тот, от чьего имени выполняется действие."""

    user_id: str
    role: str
    name: str

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS.get(self.role, frozenset())


def load_users(path: Path) -> dict[str, Principal]:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        user["id"]: Principal(user["id"], user["role"], user.get("name", user["id"]))
        for user in raw.get("users", [])
    }


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def issue_token(keyring: Keyring, principal: Principal, ttl_s: int) -> str:
    body = json.dumps({"sub": principal.user_id, "exp": int(time.time()) + ttl_s}).encode()
    signature = hmac.new(keyring.token_secret(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(signature)}"


def read_token(keyring: Keyring, token: str) -> str:
    """Проверяет токен и возвращает идентификатор пользователя."""

    try:
        body_part, signature_part = token.split(".")
        body = base64.urlsafe_b64decode(body_part + "=" * (-len(body_part) % 4))
        signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
    except ValueError as error:
        raise AuthError("токен повреждён") from error
    expected = hmac.new(keyring.token_secret(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, signature):
        raise AuthError("подпись токена не сходится")
    claims = json.loads(body)
    if claims["exp"] < time.time():
        raise AuthError("срок действия токена истёк")
    return claims["sub"]


def sign_body(key_hex: str, body: bytes) -> str:
    """Подпись пакета edge-агентом: HMAC-SHA256 от тела запроса."""

    return hmac.new(bytes.fromhex(key_hex), body, hashlib.sha256).hexdigest()


def verify_source(keyring: Keyring, source_id: str, body: bytes, signature: str) -> Principal:
    key = keyring.source_keys().get(source_id)
    if key is None:
        raise AuthError(f"источник {source_id} не зарегистрирован")
    if not hmac.compare_digest(sign_body(key, body), signature):
        raise AuthError("подпись пакета не сходится")
    return Principal(source_id, "edge", source_id)
