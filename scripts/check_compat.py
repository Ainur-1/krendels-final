"""
Сравнивает соседние версии контракта и падает на несовместимом изменении.

По умолчанию проверяются все пары подряд идущих версий из contracts/events/. Можно
передать два файла явно: `check_compat.py old.schema.json new.schema.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from zero_defect.contracts.codegen import schema_files
from zero_defect.contracts.compat import compare, is_breaking


def _report(old_name: str, new_name: str, old: dict, new: dict) -> bool:
    changes = compare(old, new)
    print(f"{old_name} → {new_name}")
    for change in changes:
        print(f"  [{change.kind}] {change.where}: {change.message}")
    if not changes:
        print("  различий нет")
    return is_breaking(changes)


def main(argv: list[str]) -> int:
    if len(argv) == 2:
        old_path, new_path = map(Path, argv)
        pairs = [(old_path.name, new_path.name, old_path, new_path)]
    else:
        files = schema_files()
        pairs = [
            (old.path.name, new.path.name, old.path, new.path)
            for old, new in zip(files, files[1:], strict=False)
        ]
    breaking = False
    for old_name, new_name, old_path, new_path in pairs:
        old = json.loads(old_path.read_text(encoding="utf-8"))
        new = json.loads(new_path.read_text(encoding="utf-8"))
        breaking |= _report(old_name, new_name, old, new)
    if breaking:
        print("Найдено несовместимое изменение: нужна новая старшая версия контракта.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
