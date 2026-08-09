"""Роут: POST /clinic/appointments/{id}/complete — завершить и оплатить (complete_pay)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.appointments.application.use_cases.complete_appointment import (
    CompleteAppointmentUseCase,
    CompletePayment,
)
from app.modules.appointments.presentation.deps import get_complete_appointment_use_case
from app.modules.appointments.presentation.schemas import (
    AppointmentDetailResponse,
    CompleteAppointmentRequest,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Приём не найден"},
    409: {
        "description": "Приём уже завершён",
        "content": {
            "application/json": {
                "example": {"error": "already_completed", "message": "Приём уже завершён"}
            }
        },
    },
    422: {
        "description": (
            "Приём без позиций (и без `noCharge`), отменённый приём "
            "либо переплата по оплате"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "appointment_without_items",
                    "message": (
                        "Приём без позиций закрыть нельзя: добавьте услуги "
                        "либо закройте как бесплатный (noCharge с пояснением)"
                    ),
                }
            }
        },
    },
}


@router.post(
    "/clinic/appointments/{appointment_id}/complete",
    response_model=AppointmentDetailResponse,
    response_description="Завершённый приём с манипуляциями по зубам",
    summary="Завершить приём (и опционально оплатить)",
    responses=_ERRORS,
)
async def complete_appointment(
    appointment_id: uuid.UUID,
    body: CompleteAppointmentRequest | None = None,
    context: ClientContext = Depends(require_permission("appointments.complete_pay")),
    use_case: CompleteAppointmentUseCase = Depends(get_complete_appointment_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AppointmentDetailResponse:
    """Перевести приём в ``completed`` и, если передана оплата, провести её в кассу.

    Смена статуса и платёж атомарны: не прошла оплата (переплата/нет остатка) —
    завершение откатывается. Без блока ``payment`` приём закрывается, а остаток
    становится долгом (виден в ``GET /clinic/cash/debts``).

    Приём без единой позиции закрыть нельзя (→ 422 ``appointment_without_items``):
    иначе нулевые чеки копятся и портят среднюю выручку. Бесплатный приём
    оформляется явно — ``noCharge: true`` с ``noChargeComment``.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    payment = None
    if body is not None and body.payment is not None:
        payment = CompletePayment(
            amount=body.payment.amount_tiyin,
            method=body.payment.method,
            comment=body.payment.comment,
        )
    detail = await use_case.execute(
        context.clinic_id,
        context.account_id,
        appointment_id,
        payment,
        no_charge=body.no_charge if body is not None else False,
        no_charge_comment=body.no_charge_comment if body is not None else None,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="appointment.complete",
        entity_type="appointment",
        entity_id=appointment_id,
        payload={"paid": payment is not None, "noCharge": bool(body and body.no_charge)},
    )
    return AppointmentDetailResponse.from_detail(detail)
