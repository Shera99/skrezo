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
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
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
        "description": (
            "Нет даты приёма (и это не черновик), некорректный зуб/услуга/врач, "
            "нерабочее время либо состав длиннее суток (`duration_too_long`)"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "scheduled_at_required",
                    "message": (
                        "Укажите дату приёма: без неё запись возможна только "
                        "как черновик"
                    ),
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
    audit: AuditService = Depends(get_audit_service),
) -> AppointmentCreatedResponse:
    """Записать пациента на приём.

    ``scheduledAt`` обязателен: приём без даты бывает только черновиком
    (``status: draft``), иначе 422 ``scheduled_at_required``. Дефолта «сейчас»
    нет — запись на текущий момент передаётся явным временем, чтобы опечатка в
    имени поля не заводила приём молча и «куда попало».

    Длительность приёма — ``durationMin``, иначе **сумма по составу**: время
    услуги клиника задаёт на ОДИН зуб, поэтому позиция считается как
    ``duration × число зубов`` (работа вне зуба — ``duration × quantity``).
    Лечение трёх зубов занимает втрое больше времени, чем одного, и слот
    занимает соответствующий. Если по составу считать не по чему (нет позиций с
    услугами либо у услуг не задана длительность) — берётся длительность
    ``serviceId``, иначе 30 минут. Состав длиннее суток → 422
    ``duration_too_long``: такой объём разносят на несколько приёмов.

    Если врач назначен, приём обязан попадать в его рабочее окно
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
                status=i.status,
            )
            for i in body.items
        ],
        status=body.status,
        force=body.force,
    )
    appt = await use_case.execute(context.clinic_id, data, can_force=can_force)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="appointment.create",
        entity_type="appointment",
        entity_id=appt.id,
        payload={"patientId": str(body.patient_id), "status": appt.status.value},
    )
    return AppointmentCreatedResponse(id=appt.id, price_total_tiyin=appt.price_total_tiyin)
