"""Базовые схемы API.

``CamelModel`` — общий базовый класс: наружу отдаём camelCase (``accessToken``),
внутри работаем со snake_case. Приём тела допускает оба варианта имён.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel, to_snake


class CamelModel(BaseModel):
    """Pydantic-модель с camelCase-алиасами полей."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class NotNullFieldsModel(CamelModel):
    """``CamelModel``, отвергающая явный ``null`` в NOT NULL-полях.

    В схемах PATCH поля объявлены как ``X | None = None`` — иначе их нельзя
    было бы «не передавать». Но для колонок, объявленных в БД как NOT NULL,
    ``None`` означает не «сбросить значение», а ошибку клиента: без этой
    проверки явный ``null`` проезжает валидацию до ``setattr`` и роняет запрос
    на 500 (а при рассинхроне схемы с ORM — портит строку).

    Наследники перечисляют такие поля в ``NOT_NULL_FIELDS`` под camelCase-именами::

        class UpdateServiceRequest(NotNullFieldsModel):
            NOT_NULL_FIELDS = ("name", "price", "active")

    Проверка идёт и по camelCase, и по snake_case: ``CamelModel`` живёт с
    ``populate_by_name=True`` и принимает оба имени, поэтому сверка только по
    camelCase оставила бы обход через ``{"scheduled_at": null}``.

    Парная защита на уровне БД — :func:`app.shared.db.updates.apply_updates`.
    """

    #: Поля (camelCase), которым нельзя присвоить явный ``null``.
    NOT_NULL_FIELDS: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_nulls(cls, data: Any) -> Any:
        """Отвергнуть явный ``null`` для NOT NULL-полей — 422 вместо порчи данных."""
        if not isinstance(data, dict):
            return data
        nulled = sorted(
            {
                name
                for name in cls.NOT_NULL_FIELDS
                for key in (name, to_snake(name))
                if key in data and data[key] is None
            }
        )
        if nulled:
            raise ValueError(f"Поля нельзя обнулить: {', '.join(nulled)}")
        return data


class OkResponse(CamelModel):
    """Простой ответ-подтверждение."""

    ok: bool = True
