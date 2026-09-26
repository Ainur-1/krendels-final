"""
Справочники, которые ведёт администратор: виды дефектов и типы станков.

Вид дефекта — код, название и то, как он оценивается (каким методом обнаруживается и
где граница визуального метода). Тип станка — какую обработку он выполняет, какие
дефекты на нём возникают и какие параметры режима он передаёт в MachineLogs, с
единицами и допусками по умолчанию. Это шаблон: технолог заводит по нему конкретный
станок, а руководитель ставит станок на операцию линии.

Встроенные значения из lines/catalog.py и lines/equipment.py — начальное наполнение.
Справочник в базе обновляет эти словари на месте: остальной код читает их как раньше и
не знает, откуда пришла запись.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime

from sqlalchemy import insert, select, update

from zero_defect.lines.catalog import DEFECTS
from zero_defect.lines.equipment import MACHINE_TYPES
from zero_defect.storage.database import Database
from zero_defect.storage.database import catalog as catalog_table

CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,47}$")
TYPE_KEY = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
PARAM_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class RegistryError(ValueError):
    """Элемент справочника описан некорректно."""


def validate_defect(code: str, spec: dict) -> dict:
    problems = []
    if not CODE.match(code):
        problems.append("код дефекта: заглавная латиница, цифры и подчёркивание, до 48 знаков")
    title = str(spec.get("title", "")).strip()
    method = str(spec.get("method", "")).strip()
    if not title:
        problems.append("не указано название дефекта")
    if not method:
        problems.append("не указано, как дефект оценивается")
    if problems:
        raise RegistryError("; ".join(problems))
    return {"title": title, "method": method}


def validate_machine_type(key: str, spec: dict) -> dict:
    problems = []
    if not TYPE_KEY.match(key):
        problems.append("код типа: строчная латиница, цифры и подчёркивание")
    title = str(spec.get("title", "")).strip()
    processing = [str(p).strip() for p in spec.get("processing", []) if str(p).strip()]
    defects = [str(code).strip().upper() for code in spec.get("defect_types", [])]
    if not title:
        problems.append("не указано название типа станка")
    if not processing:
        problems.append("не указана ни одна обработка")
    unknown = [code for code in defects if code not in DEFECTS]
    if unknown:
        problems.append(f"виды дефектов не из справочника: {', '.join(unknown)}")
    parameters = {}
    for name, param in (spec.get("parameters") or {}).items():
        if not PARAM_KEY.match(name):
            problems.append(f"параметр {name}: код строчной латиницей")
            continue
        try:
            low, high = float(param["low"]), float(param["high"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"параметр {name}: нужны числа low и high")
            continue
        if low >= high:
            problems.append(f"параметр {name}: нижняя граница допуска не меньше верхней")
        parameters[name] = {
            "title": str(param.get("title") or name).strip(),
            "unit": str(param.get("unit", "")).strip(),
            "low": low,
            "high": high,
        }
    if problems:
        raise RegistryError("; ".join(problems))
    return {
        "title": title,
        "processing": processing,
        "defect_types": defects,
        "parameters": parameters,
    }


class Registry:
    """Виды дефектов и типы станков в базе поверх встроенного наполнения."""

    KINDS = {"defect": DEFECTS, "machine_type": MACHINE_TYPES}

    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        with self.database.engine.connect() as conn:
            rows = conn.execute(
                select(catalog_table.c.kind, catalog_table.c.code, catalog_table.c.config).where(
                    catalog_table.c.kind.in_(tuple(self.KINDS))
                )
            ).all()
        with self._lock:
            for kind, code, raw in rows:
                self.KINDS[kind][code] = json.loads(raw)

    def save(self, kind: str, code: str, spec: dict, author: str, create: bool) -> dict:
        target = self.KINDS[kind]
        value = (
            validate_defect(code, spec) if kind == "defect" else validate_machine_type(code, spec)
        )
        if create and code in target:
            raise RegistryError(f"«{code}» уже есть в справочнике")
        if not create and code not in target:
            raise RegistryError(f"«{code}» в справочнике нет")
        row = {
            "config": json.dumps(value, ensure_ascii=False),
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": author,
        }
        with self.database.engine.begin() as conn:
            exists = conn.execute(
                select(catalog_table.c.code).where(
                    catalog_table.c.kind == kind, catalog_table.c.code == code
                )
            ).first()
            if exists:
                conn.execute(
                    update(catalog_table)
                    .where(catalog_table.c.kind == kind, catalog_table.c.code == code)
                    .values(**row)
                )
            else:
                conn.execute(insert(catalog_table).values(kind=kind, code=code, **row))
        with self._lock:
            target[code] = value
        return value
