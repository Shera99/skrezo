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
        doctor_membership_id: uuid.UUID | None,
        own_membership_id: uuid.UUID | None,
        page: int,
        page_size: int,
    ) -> PaymentsPage:
        """Операции кассы за период.

        ``doctor_membership_id`` — фильтр по врачу приёма (касса врача);
        ``own_membership_id`` — RBAC-сужение до своих операций (``recorded_by``).
        Это разные оси: работник с ``view_own`` может смотреть свои платежи по
        приёмам любого врача.
        """
        tz = resolve_tz(await self._cash.clinic_timezone(clinic_id))
        start, end = period_bounds(date_from, date_to, tz)
        records, total = await self._cash.list_payments(
            clinic_id,
            date_from=start,
            date_to=end,
            patient_id=patient_id,
            method=method,
            doctor_membership_id=doctor_membership_id,
            own_membership_id=own_membership_id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        # Из БД created_at приходит в UTC — показываем в таймзоне клиники.
        items = [replace(r, created_at=r.created_at.astimezone(tz)) for r in records]
        return PaymentsPage(items=items, total_count=total, current_page=page, page_size=page_size)
