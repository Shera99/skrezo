"""Базовые схемы API.

``CamelModel`` — общий базовый класс: наружу отдаём camelCase (``accessToken``),
внутри работаем со snake_case. Приём тела допускает оба варианта имён.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Pydantic-модель с camelCase-алиасами полей."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class OkResponse(CamelModel):
    """Простой ответ-подтверждение."""

    ok: bool = True
