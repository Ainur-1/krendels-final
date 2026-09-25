"""
Реестр адаптеров внешних систем.

Адаптер подключается строкой в config/zero_defect.toml, в [integration] enabled. Для
встроенных адаптеров фабрика берётся отсюда; для стороннего достаточно указать в его
секции factory = "пакет.модуль:функция" — модуль импортируется при запуске, и ни ядро,
ни этот файл править не нужно. Фабрика получает AdapterSettings и возвращает объект с
тремя методами порта ExternalSystem.
"""

from __future__ import annotations

import importlib
import tomllib
from collections.abc import Callable
from pathlib import Path

from zero_defect.config import CONFIG_DIR, PROJECT_ROOT, AdapterSettings, Settings
from zero_defect.integration.adapters.galaktika import GalaktikaFileAdapter
from zero_defect.integration.adapters.mes_emulator import MesEmulatorAdapter
from zero_defect.integration.adapters.onec import OneCAdapter
from zero_defect.integration.model import ExternalSystem

Factory = Callable[[AdapterSettings], ExternalSystem]

BUILTIN: dict[str, Factory] = {
    "mes_emulator": MesEmulatorAdapter,
    "onec": OneCAdapter,
    "galaktika": lambda settings: GalaktikaFileAdapter(PROJECT_ROOT / "var" / "galaktika"),
}


def _factory_path(name: str, config_path: Path) -> str | None:
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    return raw.get("integration", {}).get(name, {}).get("factory")


def resolve(name: str, config_path: Path = CONFIG_DIR / "zero_defect.toml") -> Factory:
    if name in BUILTIN:
        return BUILTIN[name]
    target = _factory_path(name, config_path)
    if not target:
        raise KeyError(f"адаптер {name} не встроен и для него не указан factory")
    module_name, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def build_adapters(
    settings: Settings, config_path: Path = CONFIG_DIR / "zero_defect.toml"
) -> dict[str, ExternalSystem]:
    return {
        name: resolve(name, config_path)(settings.adapters.get(name, AdapterSettings()))
        for name in settings.integrations
    }
