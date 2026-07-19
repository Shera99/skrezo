"""Роут: GET /clinic/cash/payments — журнал кассы (view_all → вся, view_own → своя)."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.modules.cash.application.use_cases.list_payments import ListPaymentsUseCase
from app.modules.cash.presentation.access import CashReadScope, require_cash_read
from app.modules.cash.presentation.deps import get_list_payments_use_case
from app.modules.cash.presentation.schemas import PaymentsResponse
from app.shared.db.enums import PaymentMethod

router = APIRouter()


@router.get(
    "/clinic/cash/payments",
    response_model=PaymentsResponse,
    response_description="Операции кассы (последние сверху) с пагинацией",
    summary="Журнал кассы",
)
async def list_payments(
    date_from: date | None = Query(default=None, alias="from", description="С даты (tz клиники)"),
    date_to: date | None = Query(default=None, alias="to", description="По дату включительно"),
    patient_id: uuid.UUID | None = Query(default=None, alias="patientId"),
    method: PaymentMethod | None = Query(default=None, description="Фильтр по способу оплаты"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    scope: CashReadScope = Depends(require_cash_read),
    use_case: ListPaymentsUseCase = Depends(get_list_payments_use_case),
) -> PaymentsResponse:
    """Операции кассы за период с фильтрами.

    С правом ``cash.view_all`` виден журнал всей клиники, с правом
    ``cash.view_own`` — только операции, проведённые вызывающим (``recorded_by``).
    """
    result = await use_case.execute(
        scope.clinic_id,
        date_from=date_from,
        date_to=date_to,
        patient_id=patient_id,
        method=method,
        own_membership_id=scope.own_membership_id,
        page=page,
        page_size=page_size,
    )
    return PaymentsResponse.from_page(result)
