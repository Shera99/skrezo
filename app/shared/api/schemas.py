"""Базовые схемы API.

``CamelModel`` — общий базовый класс: наружу отдаём camelCase (``accessToken``),
внутри работаем со snake_case. Приём тела допускает оба варианта имён.

**Неизвестные поля тела пока только логируются.** Pydantic по умолчанию их молча
игнорирует, и это скрывает настоящие ошибки: `PATCH` приёма с полем ``items``
отвечает 200, хотя позиции не сохранились (для них своя ручка). Правильный ответ
на такое — 422 (``extra="forbid"``), но включать его вслепую значит уронить фронт
в момент деплоя на полях, которые он шлёт годами.

Поэтому переходный режим: поведение прежнее (лишнее игнорируется), но каждый
такой случай пишется в лог событием ``unknown_body_fields`` со схемой и списком
полей. Через пару недель по логу видно реальную картину, и запрет включается уже
без гадания.
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel, to_snake

logger = structlog.get_logger(__name__)


class CamelModel(BaseModel):
    """Pydantic-модель с camelCase-алиасами полей."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )

    @model_validator(mode="before")
    @classmethod
    def _log_unknown_fields(cls, data: Any) -> Any:
        """Записать неизвестные поля тела, не меняя поведения.

        Смотрим только на ``dict``: тело запроса приходит именно так, а вот
        валидация из ORM-объекта (``from_attributes``) сюда не попадает — там
        лишним полям взяться неоткуда.

        Заодно ловятся наши собственные опечатки при сборке ответа: неизвестный
        аргумент в ``Model(field=...)`` pydantic тоже проглатывает молча.
        """
        if not isinstance(data, dict):
            return data
        known = set()
        for name, field in cls.model_fields.items():
            known.add(name)
            if field.alias:
                known.add(field.alias)
        unknown = sorted(key for key in data if key not in known)
        if unknown:
            logger.warning("unknown_body_fields", schema=cls.__name__, fields=unknown)
        return data


class NotNullFieldsModel(CamelModel):
    """``CamelModel``, отвергающая явный ``null`` в NOT NULL-полях.

    В схемах PATCH поля объявлены как ``X | None = None`` — иначе их нельзя
    было бы «не передавать». Но для колонок, объявленных в БД как NOT NULL,
    ``None`` означает не «сбросить значение», а ошибку клиента: без этой
    проверки явный ``null`` проезжает валидацию до ``setattr`` и роняет запрос
    на 500 (а при рассинхроне схемы с ORM — портит строку).

    Наследники перечисляют такие поля в ``NOT_NULL_FIELDS`` под camelCase-именами::

        class UpdateServiceRequest(NotNullFieldsModel):
            NOT_NULL_FIELDS = ("name", "priceTiyin", "active")

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
