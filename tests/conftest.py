"""Фикстуры, общие для всего набора: система в пустых временных каталогах и пользователи."""

from __future__ import annotations

import pytest

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
