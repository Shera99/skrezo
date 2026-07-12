"""Реестр ORM-моделей.

Импорт пакета ``app.shared.db.models`` «прогревает» ``Base.metadata`` всеми
таблицами (по файлу на таблицу). Используется в ``alembic/env.py`` и тестах.
"""

from __future__ import annotations

import app.shared.db.models  # noqa: F401  регистрирует все таблицы в metadata
from app.shared.db.base import Base

__all__ = ["Base"]
