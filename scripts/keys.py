"""
Управление ключами: создание, смена ключа или профиля, выдача токена, ключ источника.

    uv run python scripts/keys.py init
    uv run python scripts/keys.py rotate --profile classic-v1
    uv run python scripts/keys.py token ctrl-01
    uv run python scripts/keys.py source edge-cam-07
    uv run python scripts/keys.py list
    uv run python scripts/keys.py password ctrl-01

Ключи лежат в каталоге из настройки security.keys_dir, вне репозитория. Смена ключа не
перешифровывает журнал: старые записи проверяются тем ключом, которым защищены.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from zero_defect.config import load_settings, resolve_storage_url
from zero_defect.ledger.crypto import PROFILES, Keyring
from zero_defect.security.auth import issue_token, load_users
from zero_defect.security.users import UserStore
from zero_defect.storage.database import open_database


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("list")
    rotate = sub.add_parser("rotate")
    rotate.add_argument("--profile", choices=sorted(PROFILES))
    token = sub.add_parser("token")
    token.add_argument("user_id")
    source = sub.add_parser("source")
    source.add_argument("source_id")
    password = sub.add_parser("password")
    password.add_argument("user_id")
    args = parser.parse_args()

    settings = load_settings()
    keyring = Keyring(settings.keys_dir)
    if args.command == "init":
        keyring.ensure(settings.crypto_profile)
        print(f"Хранилище ключей: {settings.keys_dir}, активный ключ {keyring.active().key_id}")
    elif args.command == "list":
        for info in keyring.keys():
            secret = "секрет есть" if keyring.has_secret(info.key_id) else "секрета нет"
            print(f"{info.key_id}  v{info.key_version}  {info.profile_id}  {info.status}  {secret}")
    elif args.command == "rotate":
        info = keyring.rotate(args.profile)
        print(f"Выпущен {info.key_id} ({info.profile_id}); прежние ключи оставлены для проверки.")
    elif args.command == "token":
        user = load_users(settings.users_path)[args.user_id]
        print(issue_token(keyring, user, settings.token_ttl_s))
    elif args.command == "source":
        print(keyring.register_source(args.source_id))
    elif args.command == "password":
        # Пароль вводится с клавиатуры и нигде не печатается; в базу ложится хеш scrypt.
        secret = getpass.getpass(f"Пароль для {args.user_id}: ")
        if len(secret) < 10 or secret != getpass.getpass("Ещё раз: "):
            print("Пароль короче 10 символов или не совпал.")
            return 1
        database = open_database(resolve_storage_url(settings))
        UserStore(database, settings.users_path).set_password(args.user_id, secret)
        print(f"Пароль для {args.user_id} задан.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
