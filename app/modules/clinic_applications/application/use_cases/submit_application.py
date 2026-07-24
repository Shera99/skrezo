"""Use-case: подать заявку клиники (второй путь после подтверждённого OTP)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ValidationAppError
from app.modules.clinic_applications.domain.models import ApplicationRow
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.modules.identity.application.services.otp_service import OtpService
from app.shared.db.enums import DoctorsCountRange


class SubmitClinicApplicationUseCase:
    def __init__(
        self,
        session: AsyncSession,
        applications: ClinicApplicationRepository,
        otp: OtpService,
    ) -> None:
        self._session = session
        self._applications = applications
        self._otp = otp

    async def execute(
        self,
        registration_token: str,
        company_name: str,
        city_id: uuid.UUID,
        doctors_count: DoctorsCountRange,
    ) -> ApplicationRow:
        """Создать заявку в статусе ``pending``.

        Телефон берётся из билета OTP (шаг 2), а не из тела запроса — так он
        гарантированно подтверждён, и заявку нельзя подать на чужой номер. Билет
        гасится только после успешного сохранения: ошибка валидации (неизвестный
        город) позволяет повторить без нового кода.

        Клиника автоматически НЕ создаётся — заявку рассматривает команда Skrezo.
        """
        phone = await self._otp.read_registration_ticket(registration_token)
        if phone is None:
            raise BadRequestError(
                "Телефон не подтверждён или заявка истекла", code="registration_not_verified"
            )
        if not await self._applications.city_exists(city_id):
            raise ValidationAppError("Город не найден", code="city_not_found")

        application = await self._applications.create(
            company_name=company_name,
            city_id=city_id,
            doctors_count=doctors_count,
            phone=phone,
        )
        await self._session.commit()
        await self._otp.discard_registration_ticket(registration_token)

        row = await self._applications.get_row(application.id)
        assert row is not None  # только что создали
        return row
