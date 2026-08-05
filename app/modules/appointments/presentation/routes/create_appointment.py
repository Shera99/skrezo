"""Роут: POST /clinic/appointments — создать приём (appointments.create)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.appointments.application.use_cases.create_appointment import (
    CreateAppointmentInput,
    CreateAppointmentUseCase,
)
from app.modules.appointments.domain.models import ToothInput
from app.modules.appointments.presentation.deps import get_create_appointment_use_case
from app.modules.appointments.presentation.schemas import (
    AppointmentCreatedResponse,
    CreateAppointmentRequest,
)
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Пациент не найден"},
    409: {
        "description": "Слот врача занят другим приёмом",
        "content": {
            "application/json": {
                "example": {
                    "error": "slot_taken",
                    "message": "У врача уже есть приём в это время",
                }
            }
        },
    },
    422: {
        "description": "Некорректный зуб/услуга/врач либо нерабочее время",
        "content": {
            "application/json": {
                "example": {
                    "error": "outside_working_hours",
                    "message": "Время вне рабочего окна врача",
                }
            }
        },
    },
}


@router.post(
    "/clinic/appointments",
    status_code=status.HTTP_201_CREATED,
    response_model=AppointmentCreatedResponse,
    summary="Создать приём с манипуляциями по зубам",
    response_description="Идентификатор приёма и его итоговая стоимость",
    responses=_ERRORS,
)
async def create_appointment(
    body: CreateAppointmentRequest,
    context: ClientContext = Depends(require_permission("appointments.create")),
    use_case: CreateAppointmentUseCase = Depends(get_create_appointment_use_case),
) -> AppointmentCreatedResponse:
    """Записать пациента на приём.

    Длительность приёма — ``durationMin``, иначе длительность услуги ``serviceId``,
    иначе 30 минут. Если врач назначен, приём обязан попадать в его рабочее окно
    (часы клиники ∩ график врача, мимо обеда и отсутствий → 422) и не пересекаться
    с другими его приёмами (→ 409). Пересечение дополнительно запрещено
    EXCLUDE-констрейнтом в БД, что закрывает гонку параллельных записей.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    data = CreateAppointmentInput(
        patient_id=body.patient_id,
        doctor_membership_id=body.doctor_membership_id,
        service_id=body.service_id,
        appointment_type=body.appointment_type,
        scheduled_at=body.scheduled_at,
        duration_min=body.duration_min,
        comment=body.comment,
        teeth=[
            ToothInput(
                tooth=t.tooth,
                service_id=t.service_id,
                price_tiyin=t.price_tiyin,
                comment=t.comment,
            )
            for t in body.teeth
        ],
        status=body.status,
    )
    appt = await use_case.execute(context.clinic_id, data)
    return AppointmentCreatedResponse(id=appt.id, price_total_tiyin=appt.price_total_tiyin)
