"""Схемы услуг клиники (прайс)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.shared.api.schemas import CamelModel
from app.shared.db.models.service import Service


class ServiceOut(CamelModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    price: int = Field(description="Целые сомы")
    duration: int | None = Field(default=None, description="Минуты")
    active: bool
    created_at: datetime

    @classmethod
    def from_orm_service(cls, s: Service) -> ServiceOut:
        return cls(
            id=s.id,
            name=s.name,
            description=s.description,
            price=s.price,
            duration=s.duration,
            active=s.active,
            created_at=s.created_at,
        )


class CreateServiceRequest(CamelModel):
    name: str = Field(min_length=1)
    description: str | None = None
    price: int = Field(ge=0, description="Целые сомы")
    duration: int | None = Field(default=None, ge=0, description="Минуты")


class UpdateServiceRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    price: int | None = Field(default=None, ge=0)
    duration: int | None = Field(default=None, ge=0)
    active: bool | None = None
