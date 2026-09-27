"""Роут: отметить, что пациенту напомнили."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.reminders.application.use_cases.mark_reminded import MarkRemindedUseCase
from app.modules.reminders.presentation.deps import get_mark_reminded_use_case
from app.modules.reminders.presentation.schemas import MarkRemindedRequest, ReminderMarkOut
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Пациент не из этой клиники или приём не его",
        "content": {
            "application/json": {
                "examples": {
                    "patient": {
                        "value": {
                            "error": "patient_not_found",
                            "message": "Пациент не найден в клинике",
                        }
                    },
                    "appointment": {
                        "value": {"error": "appointment_not_found", "message": "Приём не найден"}
                    },
                }
            }
        },
    },
}


@router.post(
    "/clinic/reminders/marks",
    response_model=ReminderMarkOut,
    status_code=status.HTTP_201_CREATED,
    response_description="Записанная отметка",
    summary="Отметить «напомнили»",
    responses=_ERRORS,
)
async def create_mark(
    body: MarkRemindedRequest,
    context: ClientContext = Depends(require_permission("reminders.manage")),
    use_case: MarkRemindedUseCase = Depends(get_mark_reminded_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ReminderMarkOut:
    """Зафиксировать, что пациенту напомнили: повод, приём-источник, комментарий.

    Отметка хранится на сервере и видна всем администраторам клиники — второму
    администратору больше не нужно угадывать, кому уже звонили. Кто отметил —
    членство вызывающего в этой клинике. Пациент должен быть пациентом клиники
    (есть её приём), приём-источник — его приёмом в этой клинике.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    record = await use_case.execute(
        context.clinic_id,
        context.account_id,
        patient_id=body.patient_id,
        kind=body.kind,
        appointment_id=body.appointment_id,
        comment=body.comment,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="reminders.mark",
        entity_type="patient",
        entity_id=body.patient_id,
        payload={"markId": str(record.id), "kind": body.kind.value},
    )
    return ReminderMarkOut.from_record(record)
