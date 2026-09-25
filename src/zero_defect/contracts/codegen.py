"""
Генерация всего, что повторяет контракт событий: модели pydantic, реестр моделей по
типу события, документация контракта и перечисления для интерфейса.

Единственный источник истины — JSON Schema в contracts/events/. Всё остальное
порождается отсюда одной командой и не правится руками; CI пересобирает файлы и
падает, если лежащие в репозитории разошлись со свежей генерацией.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from zero_defect.config import PROJECT_ROOT, SCHEMAS_DIR

# Версия генератора записывается в каждый сгенерированный файл: другая версия может
# дать другой текст при той же схеме, и по заголовку это видно сразу.
GENERATOR = "datamodel-code-generator"

GENERATED_PACKAGE = Path("src/zero_defect/contracts/generated")
CONTRACT_DOC = Path("docs/contracts.md")
CONTRACT_JS = Path("src/zero_defect/serving/static/contract.js")

_SCHEMA_NAME = re.compile(r"^v(\d+)\.(\d+)\.schema\.json$")


@dataclass(frozen=True)
class SchemaFile:
    """Файл схемы одной версии контракта."""

    version: str
    path: Path

    @property
    def module(self) -> str:
        return "v" + self.version.replace(".", "_")

    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


def schema_files(schemas_dir: Path = SCHEMAS_DIR) -> list[SchemaFile]:
    """Все версии контракта по возрастанию."""

    found = []
    for path in schemas_dir.iterdir():
        match = _SCHEMA_NAME.match(path.name)
        if match:
            found.append((int(match[1]), int(match[2]), SchemaFile(f"{match[1]}.{match[2]}", path)))
    return [item for *_, item in sorted(found, key=lambda entry: entry[:2])]


def event_definitions(schema: dict) -> dict[str, str]:
    """Тип события → имя определения в $defs, по константе поля event_type."""

    result = {}
    for name, definition in schema["$defs"].items():
        event_type = definition.get("properties", {}).get("event_type", {}).get("const")
        if event_type:
            result[event_type] = name
    return result


def generator_version() -> str:
    from importlib.metadata import version

    return version(GENERATOR)


def _run_codegen(schema: SchemaFile, output: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(schema.path),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(output),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.11",
        "--use-annotated",
        "--field-constraints",
        "--use-double-quotes",
        "--disable-timestamp",
        "--enum-field-as-literal",
        "all",
        "--use-schema-description",
        "--use-field-description",
        "--formatters",
        "ruff-format",
        "--custom-file-header",
        f"# Сгенерировано {GENERATOR} {generator_version()} из "
        f"contracts/events/{schema.path.name}.\n# Не править руками: "
        "uv run python scripts/generate_contracts.py",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


def _registry_source(schemas: list[SchemaFile]) -> str:
    lines = [
        f"# Сгенерировано scripts/generate_contracts.py ({GENERATOR} {generator_version()}).",
        "# Не править руками.",
        '"""Модели контракта событий по версиям и типам событий."""',
        "",
        "from __future__ import annotations",
        "",
        "from pydantic import BaseModel",
        "",
    ]
    for schema in schemas:
        lines.append(f"from . import {schema.module}")
    lines += ["", "MODELS: dict[str, dict[str, type[BaseModel]]] = {"]
    for schema in schemas:
        lines.append(f'    "{schema.version}": {{')
        for event_type, name in sorted(event_definitions(schema.load()).items()):
            lines.append(f'        "{event_type}": {schema.module}.{name},')
        lines.append("    },")
    lines += ["}", ""]
    return "\n".join(lines)


def _type_label(prop: dict, defs: dict) -> str:
    if "$ref" in prop:
        name = prop["$ref"].split("/")[-1]
        target = defs.get(name, {})
        if "enum" in target:
            return "одно из: " + ", ".join(f"`{value}`" for value in target["enum"])
        if target.get("format") == "date-time":
            return "время ISO 8601"
        if "const" in target:
            return f"`{target['const']}`"
        if target.get("type") == "object":
            return f"объект {name}"
        return target.get("type", name)
    if "const" in prop:
        return f"`{prop['const']}`"
    if "enum" in prop:
        return "одно из: " + ", ".join(f"`{value}`" for value in prop["enum"])
    if prop.get("type") == "array":
        return "список: " + _type_label(prop.get("items", {}), defs)
    return str(prop.get("type", "—"))


def _describe(prop: dict, defs: dict) -> str:
    text = prop.get("description")
    if not text and "$ref" in prop:
        text = defs.get(prop["$ref"].split("/")[-1], {}).get("description", "")
    return (text or "").replace("|", "/")


def _doc_source(schemas: list[SchemaFile]) -> str:
    latest = schemas[-1]
    schema = latest.load()
    defs = schema["$defs"]
    versions = ", ".join(f"`{item.version}`" for item in schemas)
    out = [
        "# Контракт событий zd-events",
        "",
        "Документ сгенерирован из JSON Schema командой "
        "`uv run python scripts/generate_contracts.py` и руками не правится. "
        f"Генератор: {GENERATOR} {generator_version()}.",
        "",
        f"Принимаемые версии: {versions}. Ниже — описание последней версии, "
        f"`{latest.version}`; схемы всех версий лежат в `contracts/events/`.",
        "",
        "Версия контракта (`schema_version`) не связана с версией анализирующего модуля "
        "(`analyzer_version`) и с версией приложения. Время поступления фиксирует "
        "принимающая система, источник его не передаёт.",
        "",
        "## Поведение при отклонениях от контракта",
        "",
        "| ситуация | поведение | код |",
        "|---|---|---|",
        "| неизвестная версия сообщения | сообщение отклоняется в карантин целиком | "
        "`unknown_schema_version` |",
        "| неизвестный тип события | отклоняется в карантин | `unknown_event_type` |",
        "| отсутствует обязательное поле | отклоняется, в ответе путь до поля | `missing_field` |",
        "| неизвестное значение перечисления | отклоняется: смысл значения неизвестен, "
        "и угадывать его нельзя | `unknown_enum_value` |",
        "| добавлено необязательное поле | принимается, поле сохраняется в исходном "
        "сообщении и в разборе не участвует | `unknown_optional_field` (предупреждение) |",
        "| несовместимое изменение схемы | ловится до выпуска: `scripts/check_compat.py` "
        "сравнивает версии в CI | `breaking` |",
        "",
        "Отклонённое сообщение не теряется: оно записывается в журнал вместе со списком "
        "ошибок и видно на странице карантина.",
        "",
        "## Генерация и проверки",
        "",
        f"Генератор: {GENERATOR} {generator_version()}, версия закреплена в `pyproject.toml`. "
        "Команда: `uv run python scripts/generate_contracts.py`. Из схем порождаются:",
        "",
        "| файл | что это |",
        "|---|---|",
        *[
            f"| `{GENERATED_PACKAGE}/{item.module}.py` | модели pydantic версии {item.version} |"
            for item in schemas
        ],
        f"| `{GENERATED_PACKAGE}/__init__.py` | реестр моделей по версии и типу события |",
        f"| `{CONTRACT_DOC}` | этот документ |",
        f"| `{CONTRACT_JS}` | перечисления контракта для интерфейса |",
        "",
        "Автоматические проверки в CI:",
        "",
        "- `scripts/check_generated.py` — пересобирает всё перечисленное во временный каталог "
        "и падает, если файлы в репозитории разошлись со схемой или поправлены руками;",
        "- `scripts/check_compat.py` — сравнивает соседние версии и падает на несовместимом "
        "изменении;",
        "- `scripts/export_openapi.py --check` — сверяет спецификацию API с кодом сервиса.",
        "",
        "Воспроизводимое несовместимое изменение, которое проверка ловит до передачи "
        "результата: `uv run python scripts/check_compat.py contracts/events/v1.1.schema.json "
        "contracts/examples/breaking_change_v1.2.schema.json` — удалено поле, поле стало "
        "обязательным, из перечисления убрано значение; команда завершается с ошибкой.",
        "",
    ]
    for event_type, name in sorted(event_definitions(schema).items()):
        definition = defs[name]
        required = set(definition.get("required", ()))
        out += [f"## `{event_type}`", ""]
        if definition.get("description"):
            out += [definition["description"], ""]
        out += ["| поле | тип | обязательное | смысл |", "|---|---|---|---|"]
        for field_name, prop in definition["properties"].items():
            mark = "да" if field_name in required else ""
            out.append(
                f"| `{field_name}` | {_type_label(prop, defs)} | {mark} | {_describe(prop, defs)} |"
            )
        out.append("")
    nested = ("ReportedDuration", "Defect", "EvidenceRef")
    out += ["## Вложенные объекты", ""]
    for name in nested:
        definition = defs[name]
        required = set(definition.get("required", ()))
        out += [f"### {name}", "", "| поле | тип | обязательное | смысл |", "|---|---|---|---|"]
        for field_name, prop in definition["properties"].items():
            mark = "да" if field_name in required else ""
            out.append(
                f"| `{field_name}` | {_type_label(prop, defs)} | {mark} | {_describe(prop, defs)} |"
            )
        out.append("")
    return "\n".join(out)


def _js_source(schemas: list[SchemaFile]) -> str:
    latest = schemas[-1].load()
    enums = {
        name: definition["enum"]
        for name, definition in latest["$defs"].items()
        if "enum" in definition
    }
    payload = {
        "versions": [item.version for item in schemas],
        "eventTypes": sorted(event_definitions(latest)),
        "enums": enums,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        f"// Сгенерировано scripts/generate_contracts.py ({GENERATOR} {generator_version()}).\n"
        "// Не править руками: интерфейс берёт отсюда перечисления контракта.\n"
        f"export const CONTRACT = {body};\n"
    )


def generate(out_root: Path = PROJECT_ROOT) -> list[Path]:
    """Порождает все зависимые файлы под out_root и возвращает их относительные пути."""

    schemas = schema_files()
    package = out_root / GENERATED_PACKAGE
    package.mkdir(parents=True, exist_ok=True)
    written = []
    # Генератор форматирует вывод ruff, а ruff берёт настройки из ближайшего
    # pyproject.toml. Поэтому файл сначала пишется во временный каталог вне проекта:
    # иначе текст зависел бы от того, куда генерируют, и сверка в CI ложно падала бы.
    with tempfile.TemporaryDirectory() as tmp:
        for schema in schemas:
            scratch = Path(tmp) / f"{schema.module}.py"
            _run_codegen(schema, scratch)
            target = package / scratch.name
            target.write_text(scratch.read_text(encoding="utf-8"), encoding="utf-8")
            written.append(GENERATED_PACKAGE / target.name)
    outputs = {
        GENERATED_PACKAGE / "__init__.py": _registry_source(schemas),
        CONTRACT_DOC: _doc_source(schemas),
        CONTRACT_JS: _js_source(schemas),
    }
    for relative, text in outputs.items():
        path = out_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(relative)
    return written
