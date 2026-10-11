"""Роут: POST /clinic-applications — подать заявку клиники (после OTP, публичный)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.modules.clinic_applications.application.use_cases.submit_application import (
    SubmitClinicApplicationUseCase,
)
from app.modules.clinic_applications.presentation.deps import get_submit_application_use_case
from app.modules.clinic_applications.presentation.schemas import (
    SubmitApplicationRequest,
    SubmitApplicationResponse,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "Телефон не подтверждён (нет/истёк билет OTP)",
        "content": {
            "application/json": {
                "example": {
                    "error": "registration_not_verified",
                    "message": "Телефон не подтверждён или заявка истекла",
                }
            }
        },
    },
    422: {
        "description": "Неизвестный город или некорректные поля",
        "content": {
            "application/json": {
                "example": {"error": "city_not_found", "message": "Город не найден"}
            }
        },
    },
}


@router.post(
    "/clinic-applications",
    response_model=SubmitApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    response_description="Заявка принята и ждёт рассмотрения (статус pending)",
    summary="Подать заявку клиники",
    responses=_ERRORS,
)
async def submit_application(
    body: SubmitApplicationRequest,
    use_case: SubmitClinicApplicationUseCase = Depends(get_submit_application_use_case),
) -> SubmitApplicationResponse:
    """Второй путь регистрации: «клиника с сотрудниками».

    Вызывается после подтверждения кода (шаг 2): телефон берётся из
    ``registrationToken``, а не из тела — заявку нельзя подать на чужой номер.
    Клиника **не создаётся автоматически**: команда Skrezo рассматривает заявку в
    админке и заводит клиента вручную.

    После записи команда получает карточку заявки в Telegram-канал; недоставка
    уведомления заявку не отменяет.
    """
    row = await use_case.execute(
        body.registration_token, body.company_name, body.city_id, body.doctors_count
    )
    return SubmitApplicationResponse.from_row(row)
