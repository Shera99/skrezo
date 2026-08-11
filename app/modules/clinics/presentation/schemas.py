"""Схемы ручного создания клиники/соло и настроек своей клиники."""

from __future__ import annotations

import uuid

from pydantic import Field, model_validator

from app.modules.clinics.application.use_cases.clinic_settings import ClinicSettingsView
from app.shared.api.schemas import CamelModel, NotNullFieldsModel
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


class ClinicSettingsResponse(CamelModel):
    """Настройки своей клиники.

    Тариф (`type`, `seatsLimit`) показан только для чтения — им управляет Skrezo;
    заметок саппорта здесь нет вовсе.
    """

    id: uuid.UUID
    name: str = Field(examples=["Клиника «Улыбка»"])
    type: ClinicType = Field(description="Тип аккаунта (только чтение): clinic|solo")
    city_id: uuid.UUID | None = None
    city_name: str | None = Field(
        default=None, description="Название города на языке запроса", examples=["Бишкек"]
    )
    address: str | None = None
    business_phone: str | None = None
    lang: Lang = Field(description="Язык клиники по умолчанию")
    timezone: str = Field(
        description="IANA-таймзона: в ней считаются лента дня, слоты и периоды отчётов",
        examples=["Asia/Bishkek"],
    )
    seats_limit: int | None = Field(
        default=None, description="Лимит мест по тарифу (только чтение)", examples=[5]
    )

    @classmethod
    def from_view(cls, view: ClinicSettingsView) -> ClinicSettingsResponse:
        c = view.clinic
        return cls(
            id=c.id,
            name=c.name,
            type=c.type,
            city_id=c.city_id,
            city_name=view.city_name,
            address=c.address,
            business_phone=c.business_phone,
            lang=c.lang,
            timezone=c.timezone,
            seats_limit=c.seats_limit,
        )


class UpdateClinicSettingsRequest(NotNullFieldsModel):
    """Правка настроек клиники: передаются только изменяемые поля.

    ``null`` осмыслен лишь для необязательных колонок (снять адрес, телефон,
    город). Название, язык и таймзона в БД NOT NULL — для них ``null`` это 422.
    Тариф и заметки саппорта через эту ручку не меняются: их ведёт Skrezo.
    """

    NOT_NULL_FIELDS = ("name", "lang", "timezone")

    name: str | None = Field(default=None, min_length=2, max_length=200)
    city_id: uuid.UUID | None = None
    address: str | None = Field(default=None, max_length=500)
    business_phone: str | None = None
    lang: Lang | None = None
    timezone: str | None = Field(
        default=None,
        description=(
            "IANA-таймзона (`Asia/Bishkek`). Неизвестная зона → 422 `invalid_timezone`: "
            "иначе расписание молча уедет в другую таймзону"
        ),
        examples=["Asia/Bishkek"],
    )
