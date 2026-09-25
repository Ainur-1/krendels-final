"""
Пути и настройки, от которых считаются все остальные модули.

Настройки читаются из config/zero_defect.toml и переопределяются переменными окружения
ZD_<СЕКЦИЯ>_<КЛЮЧ>. Ядро получает готовый объект Settings и не знает, откуда взялись
значения: так контейнер, тест и локальный запуск используют один и тот же код.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import time, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = PROJECT_ROOT / "config"
CONTRACTS_DIR = PROJECT_ROOT / "contracts"
SCHEMAS_DIR = CONTRACTS_DIR / "events"
DATA_DIR = PROJECT_ROOT / "data"
SCENARIOS_DIR = DATA_DIR / "scenarios"
REPORTS_DIR = PROJECT_ROOT / "reports"
METRICS_DIR = REPORTS_DIR / "metrics"
STATIC_DIR = Path(__file__).resolve().parent / "serving" / "static"

# Версии контракта событий, которые сервис принимает. Сообщение с другой версией
# отклоняется с кодом unknown_schema_version, а не разбирается наугад.
SUPPORTED_SCHEMA_VERSIONS = ("1.0", "1.1")

# Формат защищённой записи журнала. Не связан ни с версией контракта событий, ни с
# версией приложения: меняется только при изменении устройства самой записи.
RECORD_FORMAT_VERSION = "zd-record/1"


@dataclass(frozen=True)
class Shift:
    """Смена по расписанию: начало и конец по местному времени."""

    id: str
    start: time
    end: time

    def contains(self, moment: time) -> bool:
        if self.start <= self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end


@dataclass(frozen=True)
class AdapterSettings:
    """Настройки одного адаптера внешней системы."""

    base_url: str = ""
    timeout_s: float = 5.0
    max_attempts: int = 5


@dataclass(frozen=True)
class Settings:
    """Всё, что сервис читает из конфигурации. Неизменяемо после создания."""

    storage_path: Path
    keys_dir: Path
    crypto_profile: str = "hybrid-pq-v1"
    token_ttl_s: int = 43200
    demo: bool = True
    late_after_s: float = 300.0
    clock_skew_tolerance_s: float = 60.0
    workers: int = 1
    min_confidence: float = 0.6
    machine_window_s: float = 300.0
    shifts: tuple[Shift, ...] = ()
    timezone: timezone = timezone(timedelta(hours=3))
    integrations: tuple[str, ...] = ()
    adapters: dict[str, AdapterSettings] = field(default_factory=dict)
    users_path: Path = CONFIG_DIR / "users.toml"


def _env(section: str, key: str) -> str | None:
    return os.environ.get(f"ZD_{section.upper()}_{key.upper()}")


def _resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _parse_time(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def load_settings(path: Path | None = None, **overrides: object) -> Settings:
    """Читает настройки из файла, накладывает переменные окружения и явные значения."""

    raw = tomllib.loads((path or CONFIG_DIR / "zero_defect.toml").read_text(encoding="utf-8"))

    def pick(section: str, key: str, default: object, cast=str) -> object:
        env = _env(section, key)
        if env is not None:
            if cast is bool:
                return env.strip().lower() in {"1", "true", "yes"}
            return cast(env)
        return raw.get(section, {}).get(key, default)

    shifts_raw = raw.get("shifts", {})
    integration = raw.get("integration", {})
    enabled_env = _env("integration", "enabled")
    enabled = (
        tuple(name for name in enabled_env.split(",") if name)
        if enabled_env is not None
        else tuple(integration.get("enabled", ()))
    )
    adapters = {}
    for name in enabled:
        section = integration.get(name, {})
        adapters[name] = AdapterSettings(
            base_url=_env(name, "base_url") or section.get("base_url", ""),
            timeout_s=float(section.get("timeout_s", 5.0)),
            max_attempts=int(section.get("max_attempts", 5)),
        )

    values: dict[str, object] = {
        "storage_path": _resolve(str(pick("storage", "path", "var/zero_defect.sqlite3"))),
        "keys_dir": _resolve(str(pick("security", "keys_dir", "var/keys"))),
        "crypto_profile": str(pick("security", "crypto_profile", "hybrid-pq-v1")),
        "token_ttl_s": int(pick("security", "token_ttl_s", 43200, int)),
        "demo": bool(pick("security", "demo", True, bool)),
        "late_after_s": float(pick("ingest", "late_after_s", 300, float)),
        "clock_skew_tolerance_s": float(pick("ingest", "clock_skew_tolerance_s", 60, float)),
        "workers": int(pick("ingest", "workers", 1, int)),
        "min_confidence": float(pick("quality", "min_confidence", 0.6, float)),
        "machine_window_s": float(pick("quality", "machine_window_s", 300, float)),
        "shifts": tuple(
            Shift(item["id"], _parse_time(item["start"]), _parse_time(item["end"]))
            for item in shifts_raw.get("schedule", ())
        ),
        "timezone": timezone(timedelta(hours=float(shifts_raw.get("timezone_offset_h", 3)))),
        "integrations": enabled,
        "adapters": adapters,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]
