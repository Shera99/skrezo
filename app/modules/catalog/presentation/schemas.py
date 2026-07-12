"""Схемы ответа справочников."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.shared.api.schemas import CamelModel


class CatalogItemOut(CamelModel):
    """Элемент справочника (город/специализация) на языке запроса."""

    id: uuid.UUID = Field(description="Идентификатор записи справочника")
    name: str = Field(description="Название на языке запроса (фолбэк — ru)")
