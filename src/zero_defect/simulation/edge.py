"""
Эмуляция edge-агента: буфер у источника и доставка «хотя бы один раз».

Агент стоит рядом с камерой или станком. Пока центральный контур недоступен, он копит
сообщения у себя и отправляет их, когда связь вернётся. Если ответ на отправку
потерялся, агент отправит пакет ещё раз — поэтому центр обязан распознавать повторы, и
сценарий недоступности это проверяет.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from zero_defect.security.auth import sign_body

# Отправка пакета: (тело, заголовки) → HTTP-код. Подменяется в тестах и нагрузочном прогоне.
Sender = Callable[[bytes, dict[str, str]], int]


@dataclass
class EdgeAgent:
    """Источник с локальным буфером и подписью пакетов."""

    source_id: str
    key_hex: str
    send: Sender
    batch_size: int = 50
    buffer: list[dict] = field(default_factory=list)
    sent_batches: int = 0
    failed_attempts: int = 0

    def submit(self, messages: list[dict]) -> None:
        self.buffer.extend(messages)

    def flush(self) -> int:
        """Отправляет буфер пакетами до первой неудачи; возвращает число ушедших сообщений."""

        delivered = 0
        while self.buffer:
            batch = self.buffer[: self.batch_size]
            body = json.dumps({"events": batch}, ensure_ascii=False).encode()
            headers = {
                "Content-Type": "application/json",
                "X-Source-Id": self.source_id,
                "X-Signature": sign_body(self.key_hex, body),
            }
            try:
                status = self.send(body, headers)
            except OSError:
                status = 0
            if not 200 <= status < 300:
                self.failed_attempts += 1
                break
            del self.buffer[: len(batch)]
            delivered += len(batch)
            self.sent_batches += 1
        return delivered
