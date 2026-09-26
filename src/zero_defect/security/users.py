"""
Пользователи в базе данных: роль, пароль, признак активности.

При запуске в таблицу добавляются недостающие пользователи из config/users.toml. В
демонстрационном режиме пароль совпадает с логином (administrator / administrator):
так экран входа работает как настоящий, а показ не требует заводить пароли. В рабочем
режиме пароль задаёт администратор командой scripts/keys.py password <пользователь>;
хранится только хеш scrypt с солью, сам пароль нигде не записывается.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import insert, select, update

from zero_defect.security.auth import PERMISSIONS, Principal, load_users
from zero_defect.storage.database import Database
from zero_defect.storage.database import users as users_table

# Параметры scrypt: 2^14 итераций памяти по 8 блоков — рекомендация для интерактивного
# входа, около 50 мс на проверку пароля.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p)
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual.hex(), digest)


class UserStore:
    """Чтение и изменение пользователей."""

    def __init__(self, database: Database, seed_path: Path, demo: bool = False) -> None:
        self.database = database
        seed = load_users(seed_path)
        with database.engine.begin() as conn:
            existing = dict(
                conn.execute(select(users_table.c.user_id, users_table.c.password_hash)).all()
            )
            now = datetime.now(UTC).isoformat()
            rows = [
                {
                    "user_id": user.user_id,
                    "name": user.name,
                    "role": user.role,
                    "password_hash": None,
                    "active": True,
                    "created_at": now,
                }
                for user in seed.values()
                if user.user_id not in existing
            ]
            if rows:
                conn.execute(insert(users_table), rows)
            if demo:
                # Пароль задаётся только тем, у кого его ещё нет: заданный администратором
                # пароль демонстрационный режим не перетирает.
                for user_id in seed:
                    if not existing.get(user_id):
                        conn.execute(
                            update(users_table)
                            .where(users_table.c.user_id == user_id)
                            .values(password_hash=hash_password(user_id))
                        )

    def all(self) -> dict[str, Principal]:
        with self.database.engine.connect() as conn:
            rows = conn.execute(
                select(users_table)
                .where(users_table.c.active.is_(True))
                .order_by(users_table.c.role)
            ).mappings()
            return {
                row["user_id"]: Principal(row["user_id"], row["role"], row["name"]) for row in rows
            }

    def has_password(self, user_id: str) -> bool:
        with self.database.engine.connect() as conn:
            stored = conn.execute(
                select(users_table.c.password_hash).where(users_table.c.user_id == user_id)
            ).scalar_one_or_none()
        return bool(stored)

    def verify(self, user_id: str, password: str) -> bool:
        with self.database.engine.connect() as conn:
            stored = conn.execute(
                select(users_table.c.password_hash).where(
                    users_table.c.user_id == user_id, users_table.c.active.is_(True)
                )
            ).scalar_one_or_none()
        return check_password(password, stored)

    def set_password(self, user_id: str, password: str) -> None:
        with self.database.engine.begin() as conn:
            result = conn.execute(
                update(users_table)
                .where(users_table.c.user_id == user_id)
                .values(password_hash=hash_password(password))
            )
        if result.rowcount != 1:
            raise KeyError(user_id)

    def listing(self) -> list[dict]:
        """Все пользователи, включая отключённых, — для администрирования."""

        with self.database.engine.connect() as conn:
            rows = conn.execute(
                select(
                    users_table.c.user_id,
                    users_table.c.name,
                    users_table.c.role,
                    users_table.c.active,
                ).order_by(users_table.c.user_id)
            ).mappings()
            return [dict(row) for row in rows]

    def update(self, user_id: str, name: str, role: str, active: bool) -> None:
        if role not in PERMISSIONS or role == "edge":
            raise ValueError(f"роль {role!r} неизвестна")
        with self.database.engine.begin() as conn:
            result = conn.execute(
                update(users_table)
                .where(users_table.c.user_id == user_id)
                .values(name=name, role=role, active=active)
            )
        if result.rowcount != 1:
            raise KeyError(user_id)

    def add(self, user_id: str, name: str, role: str) -> None:
        if role not in PERMISSIONS or role == "edge":
            raise ValueError(f"роль {role!r} неизвестна")
        with self.database.engine.begin() as conn:
            conn.execute(
                insert(users_table).values(
                    user_id=user_id,
                    name=name,
                    role=role,
                    password_hash=None,
                    active=True,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
