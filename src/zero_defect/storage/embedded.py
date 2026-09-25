"""
Встроенный PostgreSQL для разработки: настоящий сервер в пользовательском кеше.

Нужен, чтобы локальный запуск и тесты шли на той же СУБД, что и в контейнере, без
установки PostgreSQL в систему. Пакет pgserver входит только в набор dev; в рабочем
развёртывании адрес базы задаётся переменной ZD_STORAGE_URL.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

_servers: dict[Path, object] = {}
_lock = threading.Lock()


def data_dir_for(project_root: Path, name: str) -> Path:
    """Каталог данных сервера: ~/.cache/zero-defect/<name>-<хеш пути проекта>.

    Не внутри проекта, потому что postgres не принимает путь к данным с пробелом, а путь
    к проекту его вполне может содержать. Хеш пути разделяет два клона проекта.
    """

    digest = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:8]
    return Path.home() / ".cache" / "zero-defect" / f"{name}-{digest}"


def embedded_url(data_dir: Path) -> str:
    """Поднимает сервер в data_dir, если он ещё не поднят, и возвращает адрес SQLAlchemy."""

    try:
        import pgserver
    except ImportError as error:  # pragma: no cover — зависит от набора зависимостей
        raise RuntimeError(
            "Встроенный PostgreSQL недоступен: установите набор dev (uv sync --extra dev) "
            "или задайте адрес базы в ZD_STORAGE_URL"
        ) from error
    data_dir = data_dir.resolve()
    with _lock:
        server = _servers.get(data_dir)
        if server is None:
            data_dir.mkdir(parents=True, exist_ok=True)
            server = pgserver.get_server(data_dir, cleanup_mode=None)
            _servers[data_dir] = server
    uri = server.get_uri()
    # pgserver отдаёт адрес через unix-сокет: postgresql://postgres:@/postgres?host=...
    return uri.replace("postgresql://", "postgresql+psycopg://", 1)
