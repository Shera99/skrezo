"""Use-case: журнал кассы (offset-пагинация, фильтры, own-скоуп)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

from app.modules.cash.application.use_cases._period import period_bounds, resolve_tz
from app.modules.cash.domain.models import PaymentsPage
from app.modules.cash.infrastructure.repositories.cash_repository import CashRepository
from app.shared.db.enums import PaymentMethod


class ListPaymentsUseCase:
    def __init__(self, cash: CashRepository) -> None:
        self._cash = cash

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        date_from: date | None,
        date_to: date | None,
        patient_id: uuid.UUID | None,
        method: PaymentMethod | None,
        own_membership_id: uuid.UUID | None,
        page: int,
        page_size: int,
    ) -> PaymentsPage:
        """Операции кассы за период; ``own_membership_id`` — фильтр «свои» (recorded_by)."""
        tz = resolve_tz(await self._cash.clinic_timezone(clinic_id))
        start, end = period_bounds(date_from, date_to, tz)
        records, total = await self._cash.list_payments(
            clinic_id,
            date_from=start,
            date_to=end,
            patient_id=patient_id,
            method=method,
            own_membership_id=own_membership_id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        # Из БД created_at приходит в UTC — показываем в таймзоне клиники.
        items = [replace(r, created_at=r.created_at.astimezone(tz)) for r in records]
        return PaymentsPage(
            items=items, total_count=total, current_page=page, page_size=page_size
        )
