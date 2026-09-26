"""Выгрузка схемы OpenAPI в ``docs/openapi.json`` — контракт API в репозитории.

Схема та же, что отдаёт живой сервис по ``/openapi.json`` (с группами разделов
ReDoc), но лежит рядом с кодом: фронт и приложение сверяются с ней без
запущенного бэкенда, а изменение контракта видно в диффе. Схема не зависит от
окружения (``.env``, ``ROOT_PATH``), поэтому файл одинаков у всех.

Свежесть проверяет тест ``tests/test_openapi_snapshot.py``: поменяли API —
выполните ``make openapi`` (и ``make postman``) и закоммитьте оба файла.

    uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Корень проекта в путь импорта, чтобы скрипт запускался как ``python scripts/...``.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402

OUTPUT = ROOT / "docs" / "openapi.json"


def main() -> None:
    schema = create_app().openapi()
    OUTPUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {len(schema['paths'])} путей → {OUTPUT}")


if __name__ == "__main__":
    main()
