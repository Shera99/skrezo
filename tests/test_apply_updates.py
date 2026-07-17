"""Юнит-тесты помощника ``apply_updates`` — вторая линия обороны (без БД).

Граница API отбивает ``null`` в NOT NULL-поля ещё на валидации (см.
``tests/api/test_null_guard_api.py``). Здесь проверяется сам помощник: он ловит
ошибку программиста (поле вне allowlist, ``None`` в required) и падает громко,
а не пишет её в модель. Allowlist побочно закрывает IDOR — ``clinic_id`` в теле
не должен уводить строку в чужую клинику.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.shared.db.updates import FieldUpdateError, apply_updates


class _Entity:
    """Простая мишень для ``setattr`` вместо ORM-модели."""

    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)

    def __getattr__(self, name: str) -> Any:  # доступ к произвольным полям в тестах
        return self.__dict__[name]


_UPDATABLE = frozenset({"name", "price", "description"})
_NOT_NULL = frozenset({"name", "price"})


def test_writes_only_listed_fields() -> None:
    entity = _Entity(name="старое", price=100, description="было")
    apply_updates(
        entity,
        {"name": "новое", "description": None},
        updatable=_UPDATABLE,
        not_null=_NOT_NULL,
        entity_name="услуги",
    )
    assert entity.name == "новое"
    assert entity.description is None  # NULLABLE — обнулять можно
    assert entity.price == 100  # не переданное поле не тронуто


def test_unknown_field_is_rejected() -> None:
    """Поле вне allowlist → отказ (в т.ч. IDOR-вектор clinic_id)."""
    entity = _Entity(clinic_id="свой")
    with pytest.raises(FieldUpdateError):
        apply_updates(
            entity,
            {"clinic_id": "чужой"},
            updatable=_UPDATABLE,
            not_null=_NOT_NULL,
            entity_name="услуги",
        )
    assert entity.clinic_id == "свой"  # значение не изменилось


def test_none_in_not_null_field_is_rejected() -> None:
    entity = _Entity(name="старое")
    with pytest.raises(FieldUpdateError):
        apply_updates(
            entity,
            {"name": None},
            updatable=_UPDATABLE,
            not_null=_NOT_NULL,
            entity_name="услуги",
        )
    assert entity.name == "старое"


def test_not_null_defaults_to_empty() -> None:
    """Без ``not_null`` любой ``None`` в разрешённое поле проходит."""
    entity = _Entity(description="было")
    apply_updates(
        entity,
        {"description": None},
        updatable=_UPDATABLE,
        entity_name="услуги",
    )
    assert entity.description is None
