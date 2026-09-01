"""Use-case: провести платёж по приёму (касса)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cash.application.services.cash_service import CashService
from app.modules.cash.domain.models import PaymentRecord
from app.modules.push.application.outbox import emit_event
from app.shared.db.enums import PaymentMethod


class RecordPaymentUseCase:
    def __init__(self, session: AsyncSession, cash: CashService) -> None:
        self._session = session
        self._cash = cash

    async def execute(
        self,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        appointment_id: uuid.UUID,
        amount: int,
        method: PaymentMethod,
        comment: str | None,
    ) -> PaymentRecord:
        record = await self._cash.record_payment(
            clinic_id=clinic_id,
            account_id=account_id,
            appointment_id=appointment_id,
            amount=amount,
            method=method,
            comment=comment,
        )
        # Событие — в этой же транзакции (outbox): владелец и менеджер увидят
        # платёж в инбоксе; откат платежа забирает и событие.
        await emit_event(
            self._session,
            clinic_id=clinic_id,
            type_="payment.created",
            entity_type="payment",
            entity_id=record.id,
            payload={
                "appointmentId": str(appointment_id),
                "patientId": str(record.patient_id),
                "amountTiyin": record.amount_tiyin,
            },
        )
        await self._session.commit()
        return record
