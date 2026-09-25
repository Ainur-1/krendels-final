"""
Импорт состава сборки из КОМПАС-3D.

Прямое подключение — через COM API КОМПАС (API7), и оно работает только под Windows с
установленным КОМПАС-3D. В MVP, как разрешает постановка, используется файл с условной
структурой сборки: идентификатор, версия, компоненты, количество и связи. Геометрии в
файле нет, и это сказано явно полем geometry: null, а не молчаливым отсутствием.

Переход к прямому подключению описан в KompasComSource: те же данные читаются из
IKompasDocument3D через дерево TopPart → Parts, результат приводится к той же
AssemblyStructure, и остальная система разницы не замечает.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from zero_defect.integration.model import IntegrationError, ReferenceData


@dataclass(frozen=True)
class AssemblyComponent:
    designation: str
    name: str
    item_type_id: str
    quantity: int
    origin: str


@dataclass(frozen=True)
class AssemblyStructure:
    """Условная структура сборки. geometry всегда None: геометрию мы не импортируем."""

    assembly_id: str
    version: str
    item_type_id: str
    components: tuple[AssemblyComponent, ...]
    links: tuple[dict, ...]
    geometry: None = None

    def to_reference(self) -> ReferenceData:
        types = {self.item_type_id: self.assembly_id}
        types.update({component.item_type_id: component.name for component in self.components})
        return ReferenceData(item_types=types)


def load_assembly(path: Path) -> AssemblyStructure:
    raw = json.loads(path.read_text(encoding="utf-8"))
    missing = [
        key for key in ("assembly_id", "version", "item_type_id", "components") if key not in raw
    ]
    if missing:
        raise IntegrationError(f"в файле сборки нет полей: {', '.join(missing)}", retryable=False)
    if "geometry" not in raw:
        raise IntegrationError("файл сборки должен явно указывать geometry: null", retryable=False)
    components = tuple(
        AssemblyComponent(
            designation=item["designation"],
            name=item["name"],
            item_type_id=item["item_type_id"],
            quantity=int(item.get("quantity", 1)),
            origin=item.get("origin", "manufactured"),
        )
        for item in raw["components"]
    )
    for component in components:
        if component.quantity < 1:
            raise IntegrationError(
                f"количество {component.designation} должно быть не меньше 1", retryable=False
            )
    return AssemblyStructure(
        assembly_id=raw["assembly_id"],
        version=str(raw["version"]),
        item_type_id=raw["item_type_id"],
        components=components,
        links=tuple(raw.get("links", ())),
    )


class KompasComSource:
    """Прямое чтение сборки из КОМПАС-3D через COM. Доступно только под Windows.

    Имена свойств (TopPart, Parts, Marking, Standard) взяты из описания API7 и требуют
    сверки с SDK той версии КОМПАС, что стоит на предприятии: без установленного КОМПАС
    этот путь не проверен и помечен как проектное предположение.
    """

    PROG_ID = "Kompas.Application.7"

    def load(self, document_path: str) -> AssemblyStructure:
        if sys.platform != "win32":
            raise IntegrationError(
                "прямое подключение к КОМПАС-3D требует Windows и установленного КОМПАС; "
                "используйте файл условной структуры сборки",
                retryable=False,
            )
        import win32com.client  # type: ignore[import-not-found]  # noqa: PLC0415

        application = win32com.client.Dispatch(self.PROG_ID)
        document = application.Documents.Open(document_path, False, True)
        try:
            top = document.TopPart
            components = tuple(
                AssemblyComponent(
                    designation=part.Marking,
                    name=part.Name,
                    item_type_id=part.Marking,
                    quantity=1,
                    origin="purchased" if part.Standard else "manufactured",
                )
                for part in top.Parts
            )
            return AssemblyStructure(top.Marking, "com", top.Marking, components, ())
        finally:
            document.Close(0)
