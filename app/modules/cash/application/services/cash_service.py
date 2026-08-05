"""Кассовый сервис: проведение платежей и возвратов по единым правилам.

Общий для двух потребителей — ручек кассы (``/clinic/cash/*``) и завершения
приёма (``appointments.complete``), чтобы «оплатить» считалось одинаково. Методы
**не коммитят**: транзакцией владеет вызывающий use-case (завершение приёма
проводит смену статуса и оплату атомарно).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.cash.domain.models import PaymentRecord
from app.modules.cash.infrastructure.repositories.cash_repository import CashRepository
from app.shared.db.enums import PaymentKind, PaymentMethod


class CashService:
    def __init__(self, cash: CashRepository) -> None:
        self._cash = cash

    async def record_payment(
        self,
        *,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        appointment_id: uuid.UUID,
        amount: int,
        method: PaymentMethod,
        comment: str | None,
    ) -> PaymentRecord:
        """Провести платёж по приёму.

        Приём обязан быть из этой клиники (иначе чужая касса). Переплата запрещена:
        ``amount`` не больше остатка (``price_total − оплачено``); у полностью
        оплаченного приёма или приёма с нулевой ценой платить нечего. Кассир
        (``recorded_by``) — членство вызывающего в этой клинике.
        """
        billing = await self._cash.get_billing(clinic_id, appointment_id)
        if billing is None:
            raise NotFoundError("Приём не найден", code="appointment_not_found")
        if amount <= 0:
            raise ValidationAppError("Сумма должна быть больше нуля", code="invalid_amount")
        if billing.outstanding_tiyin <= 0:
            raise ValidationAppError("По приёму нет неоплаченного остатка", code="nothing_to_pay")
        if amount > billing.outstanding_tiyin:
            raise ValidationAppError(
                "Сумма больше остатка по приёму",
                code="overpayment",
                extra={"outstandingTiyin": billing.outstanding_tiyin},
            )
        recorded_by = await self._cash.own_membership(account_id, clinic_id)
        return await self._cash.create_payment(
            clinic_id=clinic_id,
            appointment_id=appointment_id,
            patient_id=billing.patient_id,
            amount=amount,
            method=method,
            kind=PaymentKind.PAYMENT,
            recorded_by_membership_id=recorded_by,
            refunds_payment_id=None,
            comment=comment,
        )

    async def refund_payment(
        self,
        *,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        payment_id: uuid.UUID,
        amount: int | None = None,
        comment: str | None = None,
    ) -> PaymentRecord:
        """Провести возврат по платежу — частичный и, при необходимости, не первый.

        «Из трёх услуг переделали одну» — частый случай, поэтому возврат
        ограничен не признаком «уже возвращали», а **остатком платежа**:
        ``сумма платежа − уже возвращённое``. ``amount`` не передан — возвращаем
        весь остаток (привычный полный возврат). Возвращать нечего → 409, сумма
        больше остатка → 422. Возврат по возврату запрещён: сторнируют приход.

        Строка платежа берётся под блокировку — два параллельных возврата иначе
        увидели бы один остаток и вместе перевалили бы за сумму платежа.
        """
        payment = await self._cash.get_payment(clinic_id, payment_id, for_update=True)
        if payment is None:
            raise NotFoundError("Платёж не найден", code="payment_not_found")
        if payment.kind is not PaymentKind.PAYMENT:
            raise ValidationAppError(
                "Возврат можно провести только по платежу", code="not_a_payment"
            )

        refundable = payment.amount_tiyin - await self._cash.refunded_total(payment_id)
        if refundable <= 0:
            raise ConflictError("Платёж уже возвращён полностью", code="already_refunded")
        amount = refundable if amount is None else amount
        if amount <= 0:
            raise ValidationAppError("Сумма должна быть больше нуля", code="invalid_amount")
        if amount > refundable:
            raise ValidationAppError(
                "Сумма больше остатка по платежу",
                code="refund_exceeds_payment",
                extra={"refundableTiyin": refundable},
            )

        recorded_by = await self._cash.own_membership(account_id, clinic_id)
        return await self._cash.create_payment(
            clinic_id=clinic_id,
            appointment_id=payment.appointment_id,
            patient_id=payment.patient_id,
            amount=amount,
            method=payment.method,
            kind=PaymentKind.REFUND,
            recorded_by_membership_id=recorded_by,
            refunds_payment_id=payment.id,
            comment=comment,
        )


def build_cash_service(session: AsyncSession) -> CashService:
    """Собрать кассовый сервис поверх сессии.

    Провайдер живёт в application-слое, чтобы соседний модуль ``appointments``
    брал сервис отсюда, а не импортировал ``cash.presentation.deps``.
    """
    return CashService(CashRepository(session))
