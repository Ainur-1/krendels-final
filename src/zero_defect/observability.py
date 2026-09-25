"""
Телеметрия сервиса: счётчики и распределения времени обработки.

Ядро пишет в порт Telemetry и не знает, куда уходят числа. В MVP это InMemoryTelemetry,
которую отдаёт /api/ops/metrics. Экспорт через OpenTelemetry — ещё одна реализация
того же порта (OtelTelemetry в docs/architecture.md): ядро и эндпоинты при этом не
меняются, меняется одна строка сборки сервиса.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Protocol


class Telemetry(Protocol):
    def count(self, name: str, value: int = 1, **attributes: str) -> None: ...

    def observe(self, name: str, value: float, **attributes: str) -> None: ...

    def snapshot(self) -> dict: ...


class InMemoryTelemetry:
    """Хранит счётчики и последние значения распределений в памяти процесса."""

    # Сколько последних значений держать на распределение: этого хватает на процентили
    # демонстрации и не даёт памяти расти под нагрузкой.
    WINDOW = 2048

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._samples: dict[str, list[float]] = defaultdict(list)

    @staticmethod
    def _key(name: str, attributes: dict[str, str]) -> str:
        if not attributes:
            return name
        labels = ",".join(f"{key}={value}" for key, value in sorted(attributes.items()))
        return f"{name}{{{labels}}}"

    def count(self, name: str, value: int = 1, **attributes: str) -> None:
        with self._lock:
            self._counters[self._key(name, attributes)] += value

    def observe(self, name: str, value: float, **attributes: str) -> None:
        with self._lock:
            samples = self._samples[self._key(name, attributes)]
            samples.append(value)
            if len(samples) > self.WINDOW:
                del samples[: len(samples) - self.WINDOW]

    def snapshot(self) -> dict:
        with self._lock:
            distributions = {}
            for key, samples in self._samples.items():
                ordered = sorted(samples)
                distributions[key] = {
                    "count": len(ordered),
                    "p50": ordered[len(ordered) // 2],
                    "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
                    "max": ordered[-1],
                }
            return {"counters": dict(self._counters), "distributions": distributions}
