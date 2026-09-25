"""
Шина безопасности: единая точка проверки подлинности, прав и аудита.

Любое действие проходит три шага в одном порядке: кто это (токен пользователя или
подпись источника), можно ли ему (права роли), и если действие критическое — запись
в неизменяемый журнал с автором и подробностями. Отказ в доступе тоже критическое
действие: попытки превысить права видны в журнале так же, как решения.
"""

from __future__ import annotations

from collections.abc import Callable

from zero_defect.ledger.crypto import Keyring
from zero_defect.security.auth import (
    CRITICAL,
    AuthError,
    Principal,
    read_token,
    verify_source,
)
from zero_defect.security.users import UserStore

AuditSink = Callable[[str, Principal, dict], None]


class SecurityBus:
    """Проверяет каждого, кто обращается к системе, и пишет аудит."""

    def __init__(self, keyring: Keyring, users: UserStore, audit: AuditSink) -> None:
        self.keyring = keyring
        self.store = users
        self._audit = audit

    @property
    def users(self) -> dict[str, Principal]:
        return self.store.all()

    def user(self, user_id: str) -> Principal:
        principal = self.store.all().get(user_id)
        if principal is None:
            raise AuthError(f"пользователь {user_id} не найден")
        return principal

    def login(self, user_id: str, password: str) -> Principal:
        """Вход по паролю. Ошибка одна на любой случай, чтобы не подсказывать, что не так."""

        if not self.store.verify(user_id, password):
            raise AuthError("неверный пользователь или пароль")
        return self.user(user_id)

    def from_token(self, token: str | None) -> Principal:
        if not token:
            raise AuthError("нужен токен доступа")
        return self.user(read_token(self.keyring, token))

    def from_source(self, source_id: str | None, body: bytes, signature: str | None) -> Principal:
        if not source_id or not signature:
            raise AuthError("нужны X-Source-Id и X-Signature")
        return verify_source(self.keyring, source_id, body, signature)

    def authorize(
        self, principal: Principal, permission: str, action: str, details: dict | None = None
    ) -> None:
        """Пропускает действие или бросает AuthError(403); критическое — пишет в журнал."""

        details = details or {}
        if not principal.can(permission):
            self._audit(
                "denied", principal, {"action": action, "permission": permission, **details}
            )
            raise AuthError(f"роли {principal.role} действие «{action}» не разрешено", status=403)
        if permission in CRITICAL:
            self._audit(action, principal, details)
