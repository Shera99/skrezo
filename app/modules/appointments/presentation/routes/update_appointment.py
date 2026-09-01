"""Роут: PATCH /clinic/appointments/{id} — обновить приём (appointments.edit)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import has_permission, require_permission
from app.modules.appointments.application.use_cases.update_appointment import (
    UpdateAppointmentUseCase,
)
from app.modules.appointments.presentation.deps import get_update_appointment_use_case
from app.modules.appointments.presentation.schemas import (
    AppointmentDetailResponse,
    UpdateAppointmentRequest,
)
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Нет права на откат статуса или на запись вне графика",
        "content": {
            "application/json": {
                "example": {
                    "error": "force_booking_forbidden",
                    "message": "Нужно право на запись вне графика врача",
                }
            }
        },
    },
    404: {"description": "Приём не найден"},
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
            "Врач/услуга не из этой клиники, нерабочее время "
            "либо закрытие приёма без позиций"
        ),
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


@router.patch(
    "/clinic/appointments/{appointment_id}",
    response_model=AppointmentDetailResponse,
    summary="Обновить приём (статус/комментарий/врач/дата/услуга)",
    response_description="Обновлённый приём с манипуляциями по зубам",
    responses=_ERRORS,
)
async def update_appointment(
    appointment_id: uuid.UUID,
    body: UpdateAppointmentRequest,
    context: ClientContext = Depends(require_permission("appointments.edit")),
    can_rollback: bool = Depends(has_permission("appointments.status_rollback")),
    can_force: bool = Depends(has_permission("appointments.force_booking")),
    use_case: UpdateAppointmentUseCase = Depends(get_update_appointment_use_case),
) -> AppointmentDetailResponse:
    """Изменить приём (передаются только правимые поля).

    Перенос времени, смена врача, услуги или длительности перепроверяются на
    рабочее окно (→ 422) и пересечение с другими приёмами врача (→ 409); сам приём
    из проверки исключается. Смена ``serviceId`` без явного ``durationMin``
    подтягивает длительность новой услуги — как есть, без множителя по зубам:
    состав приёма правится своей ручкой, и время по нему считается только при
    записи. Отмена (``status=cancelled``) слот
    освобождает, в проверках не участвует и обнуляет итог приёма — позиции при
    этом сохраняются, а снятие отмены пересчитывает сумму из них.

    Закрытие приёма (``status: completed``) подчиняется тому же правилу, что и
    ручка завершения: без позиций — только как бесплатный (``noCharge`` с
    ``noChargeComment``), иначе 422. ``force: true`` (право
    ``appointments.force_booking``) снимает только проверку рабочих часов.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    fields = body.model_dump(exclude_unset=True)
    # Комментарий к откату — не поле приёма: он уходит в аудит.
    rollback_comment = fields.pop("rollback_comment", None)
    # force — модификатор проверки занятости, а не колонка приёма.
    force = bool(fields.pop("force", False))
    detail = await use_case.execute(
        context.clinic_id,
        appointment_id,
        fields,
        actor_id=context.account_id,
        can_rollback=can_rollback,
        rollback_comment=rollback_comment,
        can_force=can_force,
        force=force,
    )
    return AppointmentDetailResponse.from_detail(detail)
