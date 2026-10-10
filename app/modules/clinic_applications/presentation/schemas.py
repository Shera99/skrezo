"""Схемы заявок клиник (camelCase наружу).

Очередь заявок одна, дверей две: через OTP (``SubmitApplicationRequest``,
телефон подтверждён) и с лендинга без кода (``CreateLeadRequest``, телефон не
подтверждён). В админке обе показываются одной схемой :class:`ApplicationItem`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from app.modules.clinic_applications.application.use_cases.list_applications import (
    ApplicationsResult,
)
from app.modules.clinic_applications.domain.models import ApplicationRow
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import ClinicApplicationStatus, DoctorsCountRange


def _blank_to_none(value: Any) -> Any:
    """Обрезать пробелы у строки; пустая (или из одних пробелов) → ``None``."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class SubmitApplicationRequest(CamelModel):
    """Заявка клиники после подтверждённого OTP (шаг 2).

    Телефон в теле не передаётся — он берётся из ``registrationToken``, чтобы
    заявку нельзя было подать на чужой (неподтверждённый) номер.
    """

    registration_token: str = Field(description="Билет из ответа /auth/otp/verify")
    company_name: str = Field(min_length=2, description="Название компании", examples=["Дента+"])
    city_id: uuid.UUID = Field(description="Город из GET /cities")
    doctors_count: DoctorsCountRange = Field(
        description="Количество врачей: 2-5 | 6-10 | 10+", examples=["6-10"]
    )


class CreateLeadRequest(CamelModel):
    """Заявка с лендинга без подтверждения телефона (имя и телефон).

    Лендинг спрашивает только два поля — ``fullName`` и ``phone``; остальные
    необязательны и остаются в контракте для других источников. Пустые строки и
    строки из одних пробелов приравниваются к отсутствию значения.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"fullName": "Айгүл Асанова", "phone": "+996700123456"},
                {
                    "fullName": "Айгүл Асанова",
                    "phone": "0700 123 456",
                    "clinicName": "Дента+",
                    "doctorsCountRange": "6-10",
                    "comment": "Перезвоните после 14:00",
                    "source": "instagram",
                },
            ]
        }
    )

    full_name: str = Field(
        min_length=2,
        max_length=200,
        description="Имя контактного лица (2–200 символов)",
        examples=["Айгүл Асанова"],
    )
    phone: str = Field(
        description="Телефон в любом разумном виде; сервер приводит его к E.164",
        examples=["+996700123456", "0700 123 456"],
    )
    clinic_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=200,
        description="Название клиники (2–200 символов). Необязательно: лендинг его не спрашивает",
        examples=["Дента+"],
    )
    doctors_count_range: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "Сколько врачей — свободный текст. В очередь попадает диапазон "
            "`2-5` | `6-10` | `10+`, только если строка после обрезки пробелов "
            "совпала с ним ровно; иначе поле в заявке остаётся пустым"
        ),
        examples=["6-10"],
    )
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Комментарий человека; его читает менеджер, метки трафика сюда не кладут",
        examples=["Перезвоните после 14:00"],
    )
    source: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Метка трафика — `utm_source` или referrer как пришли с формы; нужна, "
            "чтобы знать, какой канал приводит заявки"
        ),
        examples=["instagram"],
    )

    @field_validator(
        "full_name",
        "clinic_name",
        "doctors_count_range",
        "comment",
        "source",
        mode="before",
    )
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return _blank_to_none(value)


class LeadCreatedResponse(CamelModel):
    """Подтверждение приёма заявки с лендинга."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3f6c1b2a-8d4e-4f5a-9b7c-1e2d3c4b5a60",
                "createdAt": "2026-10-10T09:15:00+00:00",
            }
        }
    )

    id: uuid.UUID = Field(description="Идентификатор заявки в очереди (clinic_application.id)")
    created_at: datetime = Field(description="Когда заявка принята")


class ApplicationItem(CamelModel):
    """Заявка клиники в списке/карточке админки (обе двери очереди).

    ``phoneVerified = true`` — заявка из приложения (телефон подтверждён кодом,
    название/город/размер заполнены). ``phoneVerified = false`` — лид с лендинга:
    известны имя и телефон, остальное может быть пустым, а по номеру нельзя
    отправлять доступы.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "3f6c1b2a-8d4e-4f5a-9b7c-1e2d3c4b5a60",
                    "companyName": None,
                    "cityId": None,
                    "cityName": None,
                    "doctorsCount": None,
                    "fullName": "Айгүл Асанова",
                    "phone": "+996700123456",
                    "phoneVerified": False,
                    "source": "instagram",
                    "comment": None,
                    "status": "pending",
                    "convertedClinicId": None,
                    "reviewedByAccountId": None,
                    "reviewedByName": None,
                    "reviewedAt": None,
                    "createdAt": "2026-10-10T09:15:00+00:00",
                },
                {
                    "id": "8a1d4c7e-2b5f-4e90-a3c6-5d7e9f102b34",
                    "companyName": "Дента+",
                    "cityId": "0f9b7c1e-2a3d-4b5c-8e9f-1a2b3c4d5e6f",
                    "cityName": "Бишкек",
                    "doctorsCount": "6-10",
                    "fullName": None,
                    "phone": "+996555000111",
                    "phoneVerified": True,
                    "source": None,
                    "comment": None,
                    "status": "reviewed",
                    "convertedClinicId": "5b7d9f1a-3c4e-4a6b-8d0e-2f4a6c8e0b13",
                    "reviewedByAccountId": "c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6a8e10",
                    "reviewedByName": "Менеджер Skrezo",
                    "reviewedAt": "2026-10-10T11:30:00+00:00",
                    "createdAt": "2026-10-09T16:40:00+00:00",
                },
            ]
        }
    )

    id: uuid.UUID = Field(description="Идентификатор заявки")
    company_name: str | None = Field(
        description="Название клиники; у лида с лендинга бывает `null`",
        examples=["Дента+"],
    )
    city_id: uuid.UUID | None = Field(
        description="Город из справочника; у лида с лендинга всегда `null`",
    )
    city_name: str | None = Field(
        description="Название города на языке платформы; `null`, если города нет",
        examples=["Бишкек"],
    )
    doctors_count: DoctorsCountRange | None = Field(
        description="Размер клиники: `2-5` | `6-10` | `10+`; у лида — `null`, если не указан",
        examples=["6-10"],
    )
    full_name: str | None = Field(
        description=(
            "Имя контактного лица. Есть у лида с лендинга; у заявки из приложения "
            "`null` — там имя спрашивается позже"
        ),
        examples=["Айгүл Асанова"],
    )
    phone: str = Field(
        description=(
            "Телефон. Подтверждён по OTP, если `phoneVerified = true`; у лида с "
            "лендинга — как ввёл человек, после нормализации в E.164"
        ),
        examples=["+996700123456"],
    )
    phone_verified: bool = Field(
        description=(
            "`true` — телефон подтверждён кодом (заявка из приложения); `false` — "
            "лид с лендинга: по этому номеру нельзя отправлять доступы"
        ),
        examples=[False],
    )
    source: str | None = Field(
        description="Метка трафика (`utm_source` или referrer); `null` — не передана",
        examples=["instagram"],
    )
    comment: str | None = Field(
        description="Комментарий человека из формы; `null` — не писал",
        examples=["Перезвоните после 14:00"],
    )
    status: ClinicApplicationStatus = Field(
        description="pending — на рассмотрении, reviewed — рассмотрен, rejected — отклонён"
    )
    converted_clinic_id: uuid.UUID | None = Field(
        description=(
            "Клиника, заведённая по заявке (ссылка на карточку клиента); "
            "`null` — клиента ещё не завели"
        ),
    )
    reviewed_by_account_id: uuid.UUID | None = Field(
        default=None, description="Аккаунт из команды Skrezo, сменивший статус"
    )
    reviewed_by_name: str | None = Field(default=None, description="Кто из команды Skrezo")
    reviewed_at: datetime | None = Field(default=None, description="Когда сменён статус")
    created_at: datetime = Field(description="Когда заявка поступила")

    @classmethod
    def from_row(cls, r: ApplicationRow) -> ApplicationItem:
        return cls(
            id=r.id,
            company_name=r.company_name,
            city_id=r.city_id,
            city_name=r.city_name,
            doctors_count=r.doctors_count,
            full_name=r.full_name,
            phone=r.phone,
            phone_verified=r.phone_verified,
            source=r.source,
            comment=r.comment,
            status=r.status,
            converted_clinic_id=r.converted_clinic_id,
            reviewed_by_account_id=r.reviewed_by_account_id,
            reviewed_by_name=r.reviewed_by_name,
            reviewed_at=r.reviewed_at,
            created_at=r.created_at,
        )


class SubmitApplicationResponse(CamelModel):
    """Подтверждение приёма заявки (клиника создаётся вручную после рассмотрения)."""

    id: uuid.UUID
    status: ClinicApplicationStatus = Field(description="Всегда pending при подаче")
    created_at: datetime

    @classmethod
    def from_row(cls, r: ApplicationRow) -> SubmitApplicationResponse:
        return cls(id=r.id, status=r.status, created_at=r.created_at)


class ApplicationsResponse(CamelModel):
    """Список заявок с пагинацией и счётчиками по статусам."""

    list: list[ApplicationItem]
    total_count: int
    current_page: int
    page_size: int
    status_counts: dict[str, int] = Field(
        description=(
            "Число заявок по статусам (бейджи). Учитывает фильтр `phoneVerified`, "
            "но не `status` — чтобы чипы совпадали с отфильтрованным списком"
        ),
        examples=[{"pending": 3, "reviewed": 1}],
    )

    @classmethod
    def from_result(cls, result: ApplicationsResult) -> ApplicationsResponse:
        return cls(
            list=[ApplicationItem.from_row(r) for r in result.page.items],
            total_count=result.page.total_count,
            current_page=result.page.current_page,
            page_size=result.page.page_size,
            status_counts={s.value: c for s, c in result.status_counts.items()},
        )


class SetApplicationStatusRequest(CamelModel):
    """Смена статуса заявки из админки."""

    status: ClinicApplicationStatus = Field(
        description="pending | reviewed | rejected", examples=["reviewed"]
    )
