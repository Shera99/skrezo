"""Схемы админ-настройки триал-функций (camelCase наружу)."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.shared.api.schemas import CamelModel
from app.shared.db.models.permission import Permission


class TrialFeatureItem(CamelModel):
    """Право каталога с флагом доступности на триале."""

    id: uuid.UUID
    key: str = Field(examples=["schedule.view_all"])
    group_key: str = Field(examples=["schedule"])
    description: str = Field(examples=["Видеть расписание всех врачей"])
    in_trial: bool = Field(description="Доступно ли право на 30-дневном триале")

    @classmethod
    def from_permission(cls, p: Permission) -> TrialFeatureItem:
        return cls(
            id=p.id,
            key=p.key,
            group_key=p.group_key,
            description=p.description,
            in_trial=p.in_trial,
        )


class TrialFeaturesResponse(CamelModel):
    """Каталог прав с флагами триала (сгруппировать по ``groupKey`` на фронте)."""

    items: list[TrialFeatureItem]


class TrialFeatureUpdateItem(CamelModel):
    """Переключение флага у одного права."""

    key: str = Field(description="Ключ права", examples=["reports.view_all"])
    in_trial: bool = Field(description="Сделать ли доступным на триале")


class UpdateTrialFeaturesRequest(CamelModel):
    """Пакетная настройка набора триал-функций."""

    items: list[TrialFeatureUpdateItem] = Field(min_length=1)
