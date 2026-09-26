"""``docs/openapi.json`` совпадает со схемой приложения.

Файл — контракт API для фронта и приложения. Когда он отставал, фронт строил
экраны по старой схеме; тест делает отставание невозможным: поменяли API —
``make openapi`` и коммит вместе с кодом.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.main import create_app

SNAPSHOT = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def test_openapi_json_matches_the_app() -> None:
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if committed != create_app().openapi():
        # pytest.fail, а не assert: разбор разницы двух схем на полмегабайта
        # только мешает — достаточно сказать, что сделать.
        pytest.fail("docs/openapi.json устарел — выполните `make openapi` и закоммитьте файл")
