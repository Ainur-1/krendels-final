"""
Контрольное изменение записи журнала в обход приложения — для демонстрации защиты.

Меняет один байт зашифрованной записи события прямо в файле базы. После этого проверка
целостности в интерфейсе («Журнал и защита» → «Проверить целостность») показывает
номер изменённой записи. Сначала показывает, что обычное изменение журнал запрещает.

    uv run python scripts/tamper.py S03-00010
"""

from __future__ import annotations

import sys

from zero_defect.config import load_settings, resolve_storage_url
from zero_defect.simulation.tamper import tamper_event_record, update_is_prevented
from zero_defect.storage.database import open_database


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    database = open_database(resolve_storage_url(load_settings()))
    print(f"Обычное изменение запрещено журналом: {update_is_prevented(database)}")
    seq = tamper_event_record(database, argv[0])
    print(f"Запись №{seq} (событие {argv[0]}) изменена в обход приложения.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
