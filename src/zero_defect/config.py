"""
Пути, от которых считаются все остальные модули.

Пути выводятся из положения пакета, а не из текущего каталога: скрипт, тест и сервис
запускаются из разных мест, и каждый должен находить одни и те же данные.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Выданные материалы кейса. Сами данные в репозиторий не попадают: производство
# закрытое, а снимки деталей весят больше, чем имеет смысл хранить в git.
DATA_DIR = PROJECT_ROOT / "data"

REPORTS_DIR = PROJECT_ROOT / "reports"
METRICS_DIR = REPORTS_DIR / "metrics"
