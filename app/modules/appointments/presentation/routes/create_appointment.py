"""Роут: POST /clinic/appointments — создать приём (appointments.create)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import has_permission, require_permission
from app.modules.appointments.application.use_cases.create_appointment import (
    CreateAppointmentInput,
    CreateAppointmentUseCase,
)
from app.modules.appointments.domain.models import ItemInput
from app.modules.appointments.presentation.deps import get_create_appointment_use_case
from app.modules.appointments.presentation.schemas import (
    AppointmentCreatedResponse,
    CreateAppointmentRequest,
)
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Нет права на запись вне графика (`force: true`)",
        "content": {
            "application/json": {
                "example": {
                    "error": "force_booking_forbidden",
                    "message": "Нужно право на запись вне графика врача",
                }
            }
        },
    },
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
    summary="Создать приём со списком позиций",
    response_description="Идентификатор приёма и его итоговая стоимость",
    responses=_ERRORS,
)
async def create_appointment(
    body: CreateAppointmentRequest,
    context: ClientContext = Depends(require_permission("appointments.create")),
    can_force: bool = Depends(has_permission("appointments.force_booking")),
    use_case: CreateAppointmentUseCase = Depends(get_create_appointment_use_case),
) -> AppointmentCreatedResponse:
    """Записать пациента на приём.

    Длительность приёма — ``durationMin``, иначе длительность услуги ``serviceId``,
    иначе 30 минут. Если врач назначен, приём обязан попадать в его рабочее окно
    (часы клиники ∩ график врача, мимо обеда и отсутствий → 422) и не пересекаться
    с другими его приёмами (→ 409). Пересечение дополнительно запрещено
    EXCLUDE-констрейнтом в БД, что закрывает гонку параллельных записей.

    ``force: true`` (право ``appointments.force_booking``) снимает только проверку
    рабочих часов и обеда — клиники принимают вне графика постоянно. Пересечение
    приёмов и отпуск врача не обходятся никогда.
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
        items=[
            ItemInput(
                service_id=i.service_id,
                price_tiyin=i.price_tiyin,
                quantity=i.quantity,
                teeth=i.teeth,
                comment=i.comment,
            )
            for i in body.items
        ],
        status=body.status,
        force=body.force,
    )
    appt = await use_case.execute(context.clinic_id, data, can_force=can_force)
    return AppointmentCreatedResponse(id=appt.id, price_total_tiyin=appt.price_total_tiyin)
