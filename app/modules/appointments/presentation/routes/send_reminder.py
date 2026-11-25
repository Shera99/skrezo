"""Роут: POST /clinic/appointments/{id}/reminder — «напомнить сейчас»."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.ai.application.visits import VisitReminderService
from app.modules.appointments.application.use_cases.get_appointment import GetAppointmentUseCase
from app.modules.appointments.presentation.deps import (
    get_get_appointment_use_case,
    get_visit_reminder_service,
)
from app.modules.appointments.presentation.schemas import AppointmentDetailResponse
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Приём не найден",
        "content": {
            "application/json": {
                "example": {"error": "appointment_not_found", "message": "Приём не найден"}
            }
        },
    },
    409: {
        "description": "WhatsApp не подключён к клинике",
        "content": {
            "application/json": {
                "example": {
                    "error": "whatsapp_not_connected",
                    "message": "WhatsApp не подключён к клинике",
                }
            }
        },
    },
    422: {
        "description": (
            "О приёме нельзя напомнить: `reason` — `status` (не `scheduled`), `started` "
            "(время наступило), `day` (не сегодня и не завтра)"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "reminder_not_available",
                    "message": "Напомнить можно о приёме на сегодня или завтра",
                    "reason": "day",
                }
            }
        },
    },
    429: {
        "description": (
            "Напоминание по этому приёму уходило меньше 30 минут назад; `retryAfter` — "
            "через сколько секунд можно повторить"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "reminder_too_soon",
                    "message": (
                        "Напоминание по этому приёму уже отправлено — повторить можно чуть позже"
                    ),
                    "retryAfter": 1260,
                }
            }
        },
    },
}


@router.post(
    "/clinic/appointments/{appointment_id}/reminder",
    response_model=AppointmentDetailResponse,
    summary="Напомнить о приёме сейчас",
    response_description="Приём с исходом отправки в поле `reminder`",
    responses=_ERRORS,
)
async def send_reminder(
    appointment_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("reminders.manage")),
    reminders: VisitReminderService = Depends(get_visit_reminder_service),
    get_appointment: GetAppointmentUseCase = Depends(get_get_appointment_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AppointmentDetailResponse:
    """Отправить пациенту напоминание в WhatsApp, не дожидаясь расписания.

    Приём на сегодня или завтра (сутки клиники) в статусе `scheduled`, который
    ещё не начался. Текст — «накануне» для завтрашнего и «в день приёма» для
    сегодняшнего, кнопки «Приду» и «Перенести». Окно 24 часов открыто — уходит
    обычным сообщением, закрыто — одобренным шаблоном. От ступени ассистента и
    умения `reminders` не зависит.

    Отказ WhatsApp — не ошибка запроса: ответ 200, а в `reminder` статус
    `failed` и причина словами. Чаще раза в 30 минут на приём не отправляется;
    неудавшаяся отправка (`failed`) срок не занимает.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    reminder = await reminders.remind_now(context.clinic_id, appointment_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="appointment.remind",
        entity_type="appointment",
        entity_id=appointment_id,
        payload={"status": reminder.status},
    )
    detail = await get_appointment.execute(context.clinic_id, appointment_id)
    return AppointmentDetailResponse.from_detail(detail)
