"""
Каркас пакета: он импортируется, а пути указывают внутрь репозитория.

Тест нужен с первого коммита, чтобы CI с самого начала проверял сборку окружения и
импорт, а не только стиль, и чтобы pytest не завершался кодом «тестов не найдено».
"""

from __future__ import annotations

import zero_defect
from zero_defect.config import DATA_DIR, PROJECT_ROOT, REPORTS_DIR


def test_version_matches_pyproject():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{zero_defect.__version__}"' in pyproject


def test_paths_live_inside_project():
    for path in (DATA_DIR, REPORTS_DIR):
        assert path.is_relative_to(PROJECT_ROOT)
