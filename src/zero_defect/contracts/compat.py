"""
Сравнение двух версий контракта: какие изменения ломают потребителей, а какие нет.

Правила — обычные для схем сообщений. Удалить поле, сделать поле обязательным,
сменить тип или убрать значение перечисления — несовместимо: старый производитель
перестаёт проходить проверку. Добавить необязательное поле — совместимо. Добавить
значение перечисления — совместимо для новой версии, но потребитель старой версии
получит неизвестное значение, поэтому такое изменение помечается отдельно.
"""

from __future__ import annotations

from dataclasses import dataclass

BREAKING = "breaking"
CONSUMER_UPDATE = "consumer_update"
COMPATIBLE = "compatible"


@dataclass(frozen=True)
class Change:
    """Одно различие между версиями."""

    kind: str
    where: str
    message: str


def _resolved_type(prop: dict, defs: dict) -> str:
    if "$ref" in prop:
        target = defs.get(prop["$ref"].split("/")[-1], {})
        return str(target.get("type", "object"))
    return str(prop.get("type", "object"))


def _enum_of(prop: dict, defs: dict) -> list | None:
    if "$ref" in prop:
        prop = defs.get(prop["$ref"].split("/")[-1], {})
    return prop.get("enum")


def compare(old: dict, new: dict) -> list[Change]:
    """Все различия между схемами old и new, от более старой к более новой."""

    changes: list[Change] = []
    old_defs, new_defs = old["$defs"], new["$defs"]
    for name, old_def in old_defs.items():
        if name == "SchemaVersion":
            continue
        new_def = new_defs.get(name)
        if new_def is None:
            changes.append(Change(BREAKING, name, "определение удалено"))
            continue
        if "enum" in old_def:
            removed = set(old_def["enum"]) - set(new_def.get("enum", ()))
            added = [value for value in new_def.get("enum", ()) if value not in old_def["enum"]]
            for value in sorted(removed):
                changes.append(Change(BREAKING, name, f"удалено значение `{value}`"))
            for value in added:
                changes.append(Change(CONSUMER_UPDATE, name, f"добавлено значение `{value}`"))
        old_props = old_def.get("properties", {})
        new_props = new_def.get("properties", {})
        old_required = set(old_def.get("required", ()))
        new_required = set(new_def.get("required", ()))
        for field_name, old_prop in old_props.items():
            where = f"{name}.{field_name}"
            if field_name not in new_props:
                changes.append(Change(BREAKING, where, "поле удалено"))
                continue
            new_prop = new_props[field_name]
            if _resolved_type(old_prop, old_defs) != _resolved_type(new_prop, new_defs):
                changes.append(Change(BREAKING, where, "изменён тип поля"))
            old_enum, new_enum = _enum_of(old_prop, old_defs), _enum_of(new_prop, new_defs)
            if old_enum and new_enum is None and "$ref" not in old_prop:
                changes.append(Change(BREAKING, where, "снято ограничение перечисления"))
            if old_enum and new_enum and "$ref" not in old_prop:
                for value in sorted(set(old_enum) - set(new_enum)):
                    changes.append(Change(BREAKING, where, f"удалено значение `{value}`"))
        for field_name in new_props:
            if field_name in old_props:
                continue
            where = f"{name}.{field_name}"
            if field_name in new_required:
                changes.append(Change(BREAKING, where, "добавлено обязательное поле"))
            else:
                changes.append(Change(COMPATIBLE, where, "добавлено необязательное поле"))
        for field_name in sorted((new_required - old_required) & set(old_props)):
            changes.append(Change(BREAKING, f"{name}.{field_name}", "поле стало обязательным"))
    for name in new_defs:
        if name not in old_defs:
            changes.append(Change(COMPATIBLE, name, "добавлено определение"))
    return changes


def is_breaking(changes: list[Change]) -> bool:
    return any(change.kind == BREAKING for change in changes)
