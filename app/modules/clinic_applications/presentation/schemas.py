"""Схемы заявок клиник (camelCase наружу)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.modules.clinic_applications.application.use_cases.list_applications import (
    ApplicationsResult,
)
from app.modules.clinic_applications.domain.models import ApplicationRow
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import ClinicApplicationStatus, DoctorsCountRange


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


class ApplicationItem(CamelModel):
    """Заявка клиники в списке/карточке админки."""

    id: uuid.UUID
    company_name: str = Field(examples=["Дента+"])
    city_id: uuid.UUID
    city_name: str | None = Field(default=None, examples=["Бишкек"])
    doctors_count: DoctorsCountRange = Field(examples=["6-10"])
    phone: str = Field(description="Подтверждённый по OTP телефон", examples=["+996700123456"])
    status: ClinicApplicationStatus = Field(
        description="pending — на рассмотрении, reviewed — рассмотрен, rejected — отклонён"
    )
    reviewed_by_account_id: uuid.UUID | None = None
    reviewed_by_name: str | None = Field(default=None, description="Кто из команды Skrezo")
    reviewed_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, r: ApplicationRow) -> ApplicationItem:
        return cls(
            id=r.id,
            company_name=r.company_name,
            city_id=r.city_id,
            city_name=r.city_name,
            doctors_count=r.doctors_count,
            phone=r.phone,
            status=r.status,
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
        description="Число заявок по статусам (бейджи)", examples=[{"pending": 3, "reviewed": 1}]
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
