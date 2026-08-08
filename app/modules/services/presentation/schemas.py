"""Схемы услуг клиники (прайс)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import ToothStatus
from app.shared.db.models.service import Service

_OUTCOME_DESCRIPTION = (
    "В какое состояние услуга приводит зуб: `filling` (пломба), `crown` (коронка), "
    "`missing` (удаление), `implant`… По нему одонтограмма пациента обновляется "
    "САМА при завершении приёма. `null` — услуга состояние зуба не меняет "
    "(чистка, консультация, снимок); тогда карту правят вручную через "
    "`PUT /clinic/patients/{id}/teeth`"
)


class ServiceOut(CamelModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    price_tiyin: int = Field(description="Цена в тыйынах (1/100 сома)", examples=[425000])
    duration: int | None = Field(default=None, description="Минуты")
    active: bool
    tooth_outcome: ToothStatus | None = Field(
        default=None, description=_OUTCOME_DESCRIPTION, examples=["filling"]
    )
    created_at: datetime

    @classmethod
    def from_orm_service(cls, s: Service) -> ServiceOut:
        return cls(
            id=s.id,
            name=s.name,
            description=s.description,
            price_tiyin=s.price_tiyin,
            duration=s.duration,
            active=s.active,
            tooth_outcome=s.tooth_outcome,
            created_at=s.created_at,
        )


class CreateServiceRequest(CamelModel):
    name: str = Field(min_length=1)
    description: str | None = None
    price_tiyin: int = Field(ge=0, description="Цена в тыйынах (1/100 сома)", examples=[425000])
    duration: int | None = Field(default=None, ge=0, description="Минуты")
    tooth_outcome: ToothStatus | None = Field(
        default=None, description=_OUTCOME_DESCRIPTION, examples=["filling"]
    )


class UpdateServiceRequest(NotNullFieldsModel):
    """Правка услуги: передаются только изменяемые поля.

    ``null`` осмыслен лишь для NULLABLE-колонок (снять описание, длительность).
    Для NOT NULL-полей ``null`` — ошибка валидации (422).
    """

    NOT_NULL_FIELDS = ("name", "priceTiyin", "active")

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    price_tiyin: int | None = Field(default=None, ge=0, description="Цена в тыйынах")
    duration: int | None = Field(default=None, ge=0)
    active: bool | None = None
    tooth_outcome: ToothStatus | None = Field(
        default=None, description=f"{_OUTCOME_DESCRIPTION}. `null` — снять исход"
    )
