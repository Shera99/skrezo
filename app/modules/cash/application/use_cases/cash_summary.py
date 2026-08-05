"""Use-case: сводка кассы за период (приход/возврат/нетто по способам)."""

from __future__ import annotations

import uuid
from datetime import date

from app.modules.cash.application.use_cases._period import period_bounds, resolve_tz
from app.modules.cash.domain.models import CashSummary
from app.modules.cash.infrastructure.repositories.cash_repository import CashRepository


class CashSummaryUseCase:
    def __init__(self, cash: CashRepository) -> None:
        self._cash = cash

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        date_from: date | None,
        date_to: date | None,
        own_membership_id: uuid.UUID | None,
    ) -> CashSummary:
        """Итоги кассы за период; ``own_membership_id`` — только свои платежи."""
        timezone = await self._cash.clinic_timezone(clinic_id)
        start, end = period_bounds(date_from, date_to, resolve_tz(timezone))
        by_method = await self._cash.summary(
            clinic_id, date_from=start, date_to=end, own_membership_id=own_membership_id
        )
        total_income = sum(m.income_tiyin for m in by_method)
        total_refund = sum(m.refund_tiyin for m in by_method)
        return CashSummary(
            timezone=timezone,
            by_method=by_method,
            total_income_tiyin=total_income,
            total_refund_tiyin=total_refund,
            net_tiyin=total_income - total_refund,
        )
