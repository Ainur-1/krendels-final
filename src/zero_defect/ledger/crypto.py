"""
Криптографические профили и хранилище ключей.

Шифрование, подлинность и неизменность истории решаются здесь разными механизмами:
AEAD шифрует запись, подпись доказывает, кто её сделал, а цепочка хешей в журнале
не даёт незаметно убрать или переставить запись. Одно шифрование не защищает от
подмены, поэтому ни один из механизмов не подменяет другой.

Профиль — это набор алгоритмов под одним идентификатором. Каждая запись несёт
идентификатор профиля и ключа, поэтому смена профиля не требует переписывать старые
записи: они проверяются тем, чем были защищены.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric import ed25519, mldsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Длина nonce для AES-GCM, байт: стандартные 96 бит, при которых счётчик не
# пересчитывается через GHASH.
NONCE_BYTES = 12


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


@lru_cache(maxsize=64)
def _ed25519_key(raw: bytes) -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.from_private_bytes(raw)


@lru_cache(maxsize=64)
def _mldsa_key(seed: bytes) -> mldsa.MLDSA65PrivateKey:
    # Восстановление ключа из seed стоит около 0,6 мс — дороже самой подписи Ed25519,
    # поэтому объект ключа строится один раз на ключ.
    return mldsa.MLDSA65PrivateKey.from_seed_bytes(seed)


class CryptoError(Exception):
    """Ключ недоступен или защищённые данные не проходят проверку."""


@dataclass(frozen=True)
class Profile:
    """Набор алгоритмов защиты записи под одним идентификатором."""

    profile_id: str
    mechanism: str
    hash_name: str
    post_quantum: bool

    def digest(self, data: bytes) -> bytes:
        return hashlib.new(self.hash_name, data).digest()

    def new_secret(self) -> dict[str, bytes]:
        secret = {
            "data_key": AESGCM.generate_key(bit_length=256),
            "ed25519": ed25519.Ed25519PrivateKey.generate().private_bytes_raw(),
        }
        if self.post_quantum:
            secret["mldsa65"] = mldsa.MLDSA65PrivateKey.generate().private_bytes_raw()
        return secret

    def public_of(self, secret: dict[str, bytes]) -> dict[str, bytes]:
        public = {
            "ed25519": ed25519.Ed25519PrivateKey.from_private_bytes(secret["ed25519"])
            .public_key()
            .public_bytes_raw()
        }
        if self.post_quantum:
            public["mldsa65"] = (
                mldsa.MLDSA65PrivateKey.from_seed_bytes(secret["mldsa65"])
                .public_key()
                .public_bytes_raw()
            )
        return public

    def encrypt(
        self, secret: dict[str, bytes], plaintext: bytes, aad: bytes
    ) -> tuple[bytes, bytes]:
        nonce = os.urandom(NONCE_BYTES)
        return nonce, AESGCM(secret["data_key"]).encrypt(nonce, plaintext, aad)

    def decrypt(
        self, secret: dict[str, bytes], nonce: bytes, ciphertext: bytes, aad: bytes
    ) -> bytes:
        try:
            return AESGCM(secret["data_key"]).decrypt(nonce, ciphertext, aad)
        except InvalidTag as error:
            raise CryptoError("данные изменены или защищены другим ключом") from error

    def sign(self, secret: dict[str, bytes], message: bytes) -> bytes:
        classic = _ed25519_key(secret["ed25519"]).sign(message)
        if not self.post_quantum:
            return classic
        # Составная подпись: обе части обязаны сойтись. Подделка требует взломать и
        # эллиптическую кривую, и решётки одновременно — так устроены составные
        # подписи в черновике IETF LAMPS.
        quantum = _mldsa_key(secret["mldsa65"]).sign(message)
        return struct.pack(">H", len(classic)) + classic + quantum

    def verify(self, public: dict[str, bytes], message: bytes, signature: bytes) -> bool:
        try:
            if not self.post_quantum:
                ed25519.Ed25519PublicKey.from_public_bytes(public["ed25519"]).verify(
                    signature, message
                )
                return True
            (size,) = struct.unpack(">H", signature[:2])
            classic, quantum = signature[2 : 2 + size], signature[2 + size :]
            ed25519.Ed25519PublicKey.from_public_bytes(public["ed25519"]).verify(classic, message)
            mldsa.MLDSA65PublicKey.from_public_bytes(public["mldsa65"]).verify(quantum, message)
            return True
        except (InvalidSignature, ValueError, struct.error, KeyError):
            return False


PROFILES: dict[str, Profile] = {
    profile.profile_id: profile
    for profile in (
        Profile("classic-v1", "AES-256-GCM + Ed25519, SHA-256", "sha256", post_quantum=False),
        Profile(
            "hybrid-pq-v1",
            "AES-256-GCM + Ed25519 & ML-DSA-65 (FIPS 204), SHA-384",
            "sha384",
            post_quantum=True,
        ),
    )
}


@dataclass(frozen=True)
class KeyInfo:
    """Открытые сведения о ключе: их достаточно, чтобы проверить подпись."""

    key_id: str
    key_version: int
    profile_id: str
    status: str
    created_at: str
    public: dict[str, bytes]


class Keyring:
    """
    Ключи на диске, отдельно от кода и от журнала.

    Секретная часть лежит в keyring.json с правами только для владельца, открытая — в
    public.json. Подпись проверяется по открытой части, поэтому целостность журнала
    проверяема даже там, где секретных ключей нет, а расшифровать запись можно только
    при наличии секретного ключа её версии.
    """

    def __init__(self, keys_dir: Path) -> None:
        self.keys_dir = keys_dir
        self._secret_path = keys_dir / "keyring.json"
        self._public_path = keys_dir / "public.json"
        self._token_path = keys_dir / "token_secret"
        self._sources_path = keys_dir / "sources.json"
        self._secret: dict = {"active": None, "keys": {}}
        self._public: dict = {}
        self._load()

    def _load(self) -> None:
        if self._secret_path.exists():
            self._secret = json.loads(self._secret_path.read_text(encoding="utf-8"))
        if self._public_path.exists():
            self._public = json.loads(self._public_path.read_text(encoding="utf-8"))

    def _write_private(self, path: Path, text: str) -> None:
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)

    def _save(self) -> None:
        self._write_private(
            self._secret_path, json.dumps(self._secret, ensure_ascii=False, indent=2)
        )
        self._public_path.write_text(
            json.dumps(self._public, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def ensure(self, profile_id: str) -> None:
        """Создаёт первый ключ, если хранилище пустое."""

        if self._secret.get("active") is None:
            self.rotate(profile_id)
        if not self._token_path.exists():
            self._write_private(self._token_path, secrets.token_hex(32))
        if not self._sources_path.exists():
            self._write_private(self._sources_path, "{}")

    def rotate(self, profile_id: str | None = None) -> KeyInfo:
        """Выпускает новый ключ; прежние остаются для проверки и расшифрования.

        История не перешифровывается: старые записи остаются доказательством ровно в
        том виде, в каком были сделаны, и проверяются своим ключом.
        """

        profile = PROFILES[profile_id or self.active().profile_id]
        version = 1 + max((entry["key_version"] for entry in self._public.values()), default=0)
        key_id = f"zd-key-{version}"
        secret = profile.new_secret()
        created_at = datetime.now(UTC).isoformat()
        for entry in self._public.values():
            entry["status"] = "retired"
        self._secret["keys"][key_id] = {k: _b64(v) for k, v in secret.items()}
        self._secret["active"] = key_id
        self._public[key_id] = {
            "key_version": version,
            "profile_id": profile.profile_id,
            "status": "active",
            "created_at": created_at,
            "public": {k: _b64(v) for k, v in profile.public_of(secret).items()},
        }
        self._save()
        return self.info(key_id)

    def forget_secret(self, key_id: str) -> None:
        """Удаляет секретную часть ключа — так моделируется недоступный ключ."""

        self._secret["keys"].pop(key_id, None)
        self._save()

    def info(self, key_id: str) -> KeyInfo:
        entry = self._public.get(key_id)
        if entry is None:
            raise CryptoError(f"ключ {key_id} неизвестен")
        return KeyInfo(
            key_id=key_id,
            key_version=entry["key_version"],
            profile_id=entry["profile_id"],
            status=entry["status"],
            created_at=entry["created_at"],
            public={k: _unb64(v) for k, v in entry["public"].items()},
        )

    def keys(self) -> list[KeyInfo]:
        return [self.info(key_id) for key_id in self._public]

    def active(self) -> KeyInfo:
        if self._secret.get("active") is None:
            raise CryptoError("хранилище ключей не инициализировано")
        return self.info(self._secret["active"])

    def secret(self, key_id: str) -> dict[str, bytes]:
        entry = self._secret["keys"].get(key_id)
        if entry is None:
            raise CryptoError(f"секретная часть ключа {key_id} недоступна")
        return {k: _unb64(v) for k, v in entry.items()}

    def has_secret(self, key_id: str) -> bool:
        return key_id in self._secret["keys"]

    def token_secret(self) -> bytes:
        return self._token_path.read_text(encoding="utf-8").strip().encode("ascii")

    def source_keys(self) -> dict[str, str]:
        if not self._sources_path.exists():
            return {}
        return json.loads(self._sources_path.read_text(encoding="utf-8"))

    def register_source(self, source_id: str) -> str:
        """Выдаёт источнику (edge-агенту) ключ для подписи передаваемых пакетов."""

        sources = self.source_keys()
        sources[source_id] = secrets.token_hex(32)
        self._write_private(self._sources_path, json.dumps(sources, indent=2))
        return sources[source_id]
