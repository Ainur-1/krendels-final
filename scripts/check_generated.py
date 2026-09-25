"""
Сверяет сгенерированные файлы в репозитории со свежей генерацией.

Падает, если кто-то поменял схему и не перегенерировал зависимые файлы, или поправил
сгенерированный файл руками. Ошибка обнаруживается здесь, до передачи результата, а не
когда потребитель получит сообщение, которого его модели не знают.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from zero_defect.config import PROJECT_ROOT
from zero_defect.contracts.codegen import generate


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        fresh_root = Path(tmp)
        stale = []
        for relative in generate(out_root=fresh_root):
            committed = PROJECT_ROOT / relative
            fresh = (fresh_root / relative).read_text(encoding="utf-8")
            if not committed.exists() or committed.read_text(encoding="utf-8") != fresh:
                stale.append(relative)
    if stale:
        print("Сгенерированные файлы разошлись со схемой:")
        for relative in stale:
            print(f"  {relative}")
        print("Перегенерируйте: uv run python scripts/generate_contracts.py")
        return 1
    print("Сгенерированные файлы совпадают со схемой.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
