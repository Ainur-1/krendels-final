"""
Контрольное изменение записи журнала в обход приложения — для демонстрации защиты.

Меняет один байт зашифрованной записи события прямо в файле базы. После этого проверка
целостности в интерфейсе («Журнал и защита» → «Проверить целостность») показывает
номер изменённой записи. Сначала показывает, что обычное изменение журнал запрещает.

    uv run python scripts/tamper.py S03-00010
"""

from __future__ import annotations

import sys

from zero_defect.config import load_settings
from zero_defect.simulation.replay import tamper_event_record, update_is_prevented


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    path = load_settings().storage_path
    print(f"Обычное изменение запрещено журналом: {update_is_prevented(path)}")
    seq = tamper_event_record(path, argv[0])
    print(f"Запись №{seq} (событие {argv[0]}) изменена в обход приложения.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
