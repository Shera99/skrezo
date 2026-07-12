"""Схемы лид-формы."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.shared.api.schemas import CamelModel


class CreateLeadRequest(CamelModel):
    full_name: str = Field(min_length=2, description="ФИО контактного лица")
    phone: str = Field(description="Телефон (нормализуется в E.164)")
    clinic_name: str = Field(min_length=2, description="Название клиники")
    doctors_count_range: str | None = Field(default=None, description="Кол-во врачей, напр. '2-3'")
    comment: str | None = Field(default=None)


class LeadCreatedResponse(CamelModel):
    id: uuid.UUID
    created_at: datetime
