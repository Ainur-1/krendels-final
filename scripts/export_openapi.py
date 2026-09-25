"""
Выгружает спецификацию API в docs/openapi.json или сверяет с ней текущий код.

    uv run python scripts/export_openapi.py          # записать
    uv run python scripts/export_openapi.py --check  # упасть, если разошлось

Спецификация порождается из кода сервиса, поэтому документ API не может отстать от кода
незаметно: CI сверяет их на каждом пуше.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from zero_defect.config import PROJECT_ROOT, load_settings
from zero_defect.serving.api import create_app

TARGET = PROJECT_ROOT / "docs" / "openapi.json"


def render() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = load_settings(
            storage_url=f"sqlite:///{root / 'ledger.sqlite3'}",
            keys_dir=root / "keys",
            integrations=(),
            adapters={},
        )
        app = create_app(settings, adapters={}, load_demo=False)
        app.state.system.close()
        return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != text:
            print(
                "docs/openapi.json разошёлся с кодом. "
                "Выгрузите: uv run python scripts/export_openapi.py"
            )
            return 1
        print("docs/openapi.json совпадает с кодом.")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(TARGET.relative_to(PROJECT_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
