"""Use-case: провести возврат по платежу (касса)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cash.application.services.cash_service import CashService
from app.modules.cash.domain.models import PaymentRecord


class RefundPaymentUseCase:
    def __init__(self, session: AsyncSession, cash: CashService) -> None:
        self._session = session
        self._cash = cash

    async def execute(
        self, clinic_id: uuid.UUID, account_id: uuid.UUID, payment_id: uuid.UUID
    ) -> PaymentRecord:
        record = await self._cash.refund_payment(
            clinic_id=clinic_id, account_id=account_id, payment_id=payment_id
        )
        await self._session.commit()
        return record
