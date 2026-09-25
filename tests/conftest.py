"""Фикстуры, общие для всего набора: система в пустых временных каталогах и пользователи."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from zero_defect.service import QualitySystem
from zero_defect.simulation.checker import fresh_settings


@pytest.fixture()
def settings(tmp_path):
    return fresh_settings(tmp_path)


@pytest.fixture()
def system(settings):
    instance = QualitySystem(settings)
    yield instance
    instance.close()


@pytest.fixture()
def users(system):
    return system.security.users


def _postgres_server_url() -> str | None:
    """Сервер PostgreSQL для тестов: из ZD_TEST_POSTGRES_URL (CI) или встроенный (набор dev)."""

    configured = os.environ.get("ZD_TEST_POSTGRES_URL")
    if configured:
        return configured
    try:
        import pgserver  # noqa: F401
    except ImportError:
        return None
    from pathlib import Path

    from zero_defect.storage.embedded import data_dir_for, embedded_url

    return embedded_url(data_dir_for(Path(__file__).resolve().parents[1], "pgtest"))


@pytest.fixture(scope="session")
def postgres_server():
    return _postgres_server_url()


@pytest.fixture(params=["sqlite", "postgresql"])
def database_url(request, tmp_path, postgres_server):
    """Пустая база на каждый тест — SQLite в файле или отдельная база в PostgreSQL."""

    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'test.sqlite3'}"
        return
    if postgres_server is None:
        pytest.skip("PostgreSQL недоступен: задайте ZD_TEST_POSTGRES_URL или поставьте набор dev")
    name = f"zd_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(postgres_server, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    base, _, query = postgres_server.partition("?")
    url = base.rsplit("/", 1)[0] + f"/{name}" + (f"?{query}" if query else "")
    yield url
    from zero_defect.storage.database import close_database

    close_database(url)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()
