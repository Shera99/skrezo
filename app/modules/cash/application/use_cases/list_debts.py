"""Use-case: долги по завершённым приёмам (остаток > 0)."""

from __future__ import annotations

import uuid
from dataclasses import replace

from app.modules.cash.application.use_cases._period import resolve_tz
from app.modules.cash.domain.models import DebtsList
from app.modules.cash.infrastructure.repositories.cash_repository import CashRepository


class ListDebtsUseCase:
    def __init__(self, cash: CashRepository) -> None:
        self._cash = cash

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        patient_id: uuid.UUID | None,
        own_membership_id: uuid.UUID | None,
    ) -> DebtsList:
        """Завершённые приёмы клиники с ненулевым остатком.

        ``own_membership_id`` (view_own) ограничивает выборку приёмами, где
        вызывающий — лечащий врач (``doctor_membership``).
        """
        timezone = await self._cash.clinic_timezone(clinic_id)
        tz = resolve_tz(timezone)
        rows = await self._cash.list_debts(
            clinic_id, patient_id=patient_id, own_membership_id=own_membership_id
        )
        # scheduled_at из БД в UTC — показываем в таймзоне клиники.
        items = [replace(r, scheduled_at=r.scheduled_at.astimezone(tz)) for r in rows]
        return DebtsList(
            timezone=timezone,
            items=items,
            total_outstanding=sum(r.outstanding for r in items),
        )
