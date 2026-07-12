"""Юнит-тесты нормализации телефона (без БД/Redis)."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationAppError
from app.shared.domain.value_objects import normalize_phone


def test_normalize_local_kg_number() -> None:
    # Локальный формат КГ (ведущий 0) приводится к E.164.
    assert normalize_phone("0700 123 456") == "+996700123456"


def test_normalize_strips_formatting() -> None:
    # Скобки/дефисы/пробелы удаляются, результат идемпотентен.
    assert normalize_phone("+996 (700) 12-34-56") == "+996700123456"


def test_invalid_phone_raises() -> None:
    with pytest.raises(ValidationAppError):
        normalize_phone("123")
