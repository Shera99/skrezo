"""Роут: GET /clinic/cash/summary — сводка кассы (view_all → вся, view_own → своя)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.modules.cash.application.use_cases.cash_summary import CashSummaryUseCase
from app.modules.cash.presentation.access import CashReadScope, require_cash_read
from app.modules.cash.presentation.deps import get_cash_summary_use_case
from app.modules.cash.presentation.schemas import CashSummaryResponse

router = APIRouter()


@router.get(
    "/clinic/cash/summary",
    response_model=CashSummaryResponse,
    response_description="Приход/возврат/нетто по способам оплаты за период",
    summary="Сводка кассы",
)
async def cash_summary(
    date_from: date | None = Query(default=None, alias="from", description="С даты (tz клиники)"),
    date_to: date | None = Query(default=None, alias="to", description="По дату включительно"),
    scope: CashReadScope = Depends(require_cash_read),
    use_case: CashSummaryUseCase = Depends(get_cash_summary_use_case),
) -> CashSummaryResponse:
    """Итоги кассы за период.

    С правом ``cash.view_all`` — по всей клинике, с правом ``cash.view_own`` —
    только по своим платежам (``recorded_by``).
    """
    result = await use_case.execute(
        scope.clinic_id,
        date_from=date_from,
        date_to=date_to,
        own_membership_id=scope.own_membership_id,
    )
    return CashSummaryResponse.from_summary(result)
