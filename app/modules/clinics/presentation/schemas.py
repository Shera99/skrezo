"""Схемы ручного создания клиники/соло."""

from __future__ import annotations

import uuid

from pydantic import Field, model_validator

from app.shared.api.schemas import CamelModel
from app.shared.db.enums import ClinicType, Lang


class CreateClinicRequest(CamelModel):
    type: ClinicType = Field(description="Тип аккаунта: clinic|solo")
    admin_full_name: str = Field(min_length=2, description="ФИО администратора/врача")
    admin_phone: str = Field(description="Телефон администратора (= логин)")
    city_id: uuid.UUID | None = Field(default=None, description="Город (обяз. для clinic)")
    name: str | None = Field(default=None, min_length=2, description="Название (обяз. для clinic)")
    address: str | None = None
    business_phone: str | None = Field(
        default=None, description="Телефон бизнеса (обяз. для clinic)"
    )
    seats_limit: int | None = Field(default=None, ge=2, description="Лимит мест (обяз. для clinic)")
    lang: Lang = Lang.RU
    lead_id: uuid.UUID | None = Field(default=None, description="Конвертируемый лид (опц.)")

    @model_validator(mode="after")
    def _require_clinic_fields(self) -> CreateClinicRequest:
        if self.type is ClinicType.CLINIC:
            missing = [
                label
                for value, label in (
                    (self.name, "name"),
                    (self.city_id, "cityId"),
                    (self.business_phone, "businessPhone"),
                    (self.seats_limit, "seatsLimit"),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"Для клиники обязательны поля: {', '.join(missing)}")
        return self


class ClinicCreatedResponse(CamelModel):
    id: uuid.UUID
    type: ClinicType
    admin_phone: str = Field(description="Телефон владельца (= логин)")
    temp_password: str = Field(
        description="Временный пароль владельца — показать один раз и передать владельцу"
    )
