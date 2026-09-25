"""
Конфигурации линий в базе данных.

Линия — изменяемая настройка, а не доказательство, поэтому лежит в своей таблице, а не
в журнале. Но каждое её создание и изменение — критическое действие и пишется в
журнал с автором: по нему видно, кто и когда поменял порядок процесса.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime

from sqlalchemy import insert, select, update

from zero_defect.lines.model import LineConfig, default_lines
from zero_defect.storage.database import Database
from zero_defect.storage.database import lines as lines_table


class LineError(ValueError):
    """Конфигурация линии некорректна или линия не найдена."""


class LineStore:
    """Чтение и сохранение линий; кеширует разобранные конфигурации."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._cache: dict[str, LineConfig] = {}
        self._lock = threading.Lock()
        with database.engine.begin() as conn:
            if conn.execute(select(lines_table.c.line_id).limit(1)).first() is None:
                now = datetime.now(UTC).isoformat()
                conn.execute(
                    insert(lines_table),
                    [
                        {
                            "line_id": config.line_id,
                            "version": 1,
                            "config": config.to_json(),
                            "updated_at": now,
                            "updated_by": "system",
                        }
                        for config in default_lines()
                    ],
                )
        self.reload()

    def reload(self) -> None:
        with self.database.engine.connect() as conn:
            rows = conn.execute(select(lines_table.c.config, lines_table.c.version)).all()
        with self._lock:
            self._cache = {}
            for raw, version in rows:
                config = LineConfig.from_dict(json.loads(raw))
                config.version = version
                self._cache[config.line_id] = config

    def all(self) -> list[LineConfig]:
        with self._lock:
            return sorted(self._cache.values(), key=lambda config: config.line_id)

    def get(self, line_id: str) -> LineConfig | None:
        with self._lock:
            return self._cache.get(line_id)

    def for_item(self, line_id: str | None) -> LineConfig | None:
        return self.get(line_id) if line_id else None

    def save(self, config: LineConfig, author: str) -> LineConfig:
        problems = config.validate()
        if problems:
            raise LineError("; ".join(problems))
        now = datetime.now(UTC).isoformat()
        with self.database.engine.begin() as conn:
            current = conn.execute(
                select(lines_table.c.version).where(lines_table.c.line_id == config.line_id)
            ).scalar_one_or_none()
            config.version = (current or 0) + 1
            values = {
                "version": config.version,
                "config": config.to_json(),
                "updated_at": now,
                "updated_by": author,
            }
            if current is None:
                conn.execute(insert(lines_table).values(line_id=config.line_id, **values))
            else:
                conn.execute(
                    update(lines_table)
                    .where(lines_table.c.line_id == config.line_id)
                    .values(**values)
                )
        self.reload()
        return self.get(config.line_id)
