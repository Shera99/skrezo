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
        "description": "Врач/услуга не из этой клиники либо нерабочее время",
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
    use_case: UpdateAppointmentUseCase = Depends(get_update_appointment_use_case),
) -> AppointmentDetailResponse:
    """Изменить приём (передаются только правимые поля).

    Перенос времени, смена врача, услуги или длительности перепроверяются на
    рабочее окно (→ 422) и пересечение с другими приёмами врача (→ 409); сам приём
    из проверки исключается. Смена ``serviceId`` без явного ``durationMin``
    подтягивает длительность новой услуги. Отмена (``status=cancelled``) слот
    освобождает и в проверках не участвует.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    fields = body.model_dump(exclude_unset=True)
    # Комментарий к откату — не поле приёма: он уходит в аудит.
    rollback_comment = fields.pop("rollback_comment", None)
    detail = await use_case.execute(
        context.clinic_id,
        appointment_id,
        fields,
        actor_id=context.account_id,
        can_rollback=can_rollback,
        rollback_comment=rollback_comment,
    )
    return AppointmentDetailResponse.from_detail(detail)
