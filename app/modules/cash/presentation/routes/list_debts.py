"""Роут: GET /clinic/cash/debts — долги по приёмам (view_all → все, view_own → свои)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.modules.cash.application.use_cases.list_debts import ListDebtsUseCase
from app.modules.cash.presentation.access import CashReadScope, require_cash_read
from app.modules.cash.presentation.deps import get_list_debts_use_case
from app.modules.cash.presentation.schemas import DebtsResponse

router = APIRouter()


@router.get(
    "/clinic/cash/debts",
    response_model=DebtsResponse,
    response_description="Завершённые приёмы с ненулевым остатком",
    summary="Долги по приёмам",
)
async def list_debts(
    patient_id: uuid.UUID | None = Query(default=None, alias="patientId"),
    scope: CashReadScope = Depends(require_cash_read),
    use_case: ListDebtsUseCase = Depends(get_list_debts_use_case),
) -> DebtsResponse:
    """Завершённые приёмы, по которым остался долг (``price_total > оплачено``).

    С правом ``cash.view_all`` — по всей клинике, с правом ``cash.view_own`` —
    только по приёмам, где вызывающий лечащий врач (``doctor_membership``).
    """
    result = await use_case.execute(
        scope.clinic_id,
        patient_id=patient_id,
        own_membership_id=scope.own_membership_id,
    )
    return DebtsResponse.from_list(result)
