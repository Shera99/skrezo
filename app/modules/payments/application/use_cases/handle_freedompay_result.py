"""Серверный колбэк FreedomPay (``result_url``): единственный, кто проводит деньги.

Подпись сверяется до всего остального; сумма — с нашей строкой платежа (не
с тем, что прислали). Оплата = приход в журнал; оплата подписки тут же
списывает цену и открывает период, пополнение — остаётся на балансе (и, если
подписка ждёт денег, продлевает её). Повтор колбэка по уже оплаченному
платежу — «ok» без второго прихода: провайдер шлёт результат, пока не
получит ответ.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.payments.application.services.ledger_service import LedgerService
from app.modules.payments.domain.ledger import KIND_CREDIT, PURPOSE_SUBSCRIPTION, SOURCE_PROVIDER
from app.modules.payments.domain.periods import amount_matches
from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.shared.freedompay import verify

logger = structlog.get_logger(__name__)

#: Имя скрипта для подписи колбэка и нашего ответа — последний сегмент result_url.
RESULT_SCRIPT = "result"


@dataclass(frozen=True, slots=True)
class ResultReply:
    """Что ответить провайдеру: ok — принято, rejected — отменить платёж, error — не наш."""

    status: str
    description: str


class HandleFreedomPayResultUseCase:
    def __init__(
        self,
        session: AsyncSession,
        payments: PlatformPaymentRepository,
        ledger: LedgerService,
        manage: ManageSubscriptionUseCase,
    ) -> None:
        self._session = session
        self._payments = payments
        self._ledger = ledger
        self._manage = manage

    async def execute(self, form: dict[str, str]) -> ResultReply:
        if not settings.freedompay_secret_key or not verify(
            RESULT_SCRIPT, form, settings.freedompay_secret_key
        ):
            logger.error("freedompay_result_bad_signature", order_id=form.get("pg_order_id"))
            return ResultReply("error", "Подпись не сошлась")
        try:
            payment_id = uuid.UUID(str(form.get("pg_order_id") or ""))
        except ValueError:
            return ResultReply("error", "Неизвестный платёж")
        payment = await self._payments.get(payment_id)
        if payment is None:
            return ResultReply("error", "Неизвестный платёж")
        snapshot: dict[str, Any] = {k: v for k, v in form.items() if k != "pg_sig"}

        if payment.status == "paid":
            return ResultReply("ok", "Уже учтено")
        if payment.status == "refunded":
            return ResultReply("ok", "Платёж возвращён")

        provider_id = str(form.get("pg_payment_id") or "")
        if (
            payment.provider_payment_id
            and provider_id
            and provider_id != payment.provider_payment_id
        ):
            logger.error("freedompay_result_foreign_payment", order_id=str(payment.id))
            return ResultReply("error", "Платёж не соответствует заказу")
        if str(form.get("pg_result") or "") != "1":
            payment.status = "failed"
            payment.failure_code = str(form.get("pg_failure_code") or "") or None
            payment.failure_description = str(form.get("pg_failure_description") or "") or None
            payment.callback = snapshot
            await self._session.commit()
            logger.info(
                "freedompay_payment_failed", order_id=str(payment.id), code=payment.failure_code
            )
            return ResultReply("ok", "Отказ учтён")
        if not amount_matches(payment.amount_tiyin, str(form.get("pg_amount") or "")):
            payment.status = "failed"
            payment.failure_description = f"сумма не совпала: {form.get('pg_amount')}"
            payment.callback = snapshot
            await self._session.commit()
            logger.error("freedompay_amount_mismatch", order_id=str(payment.id))
            return ResultReply("rejected", "Сумма не совпадает с заказом")

        now = datetime.now(UTC)
        payment.status = "paid"
        payment.paid_at = now
        payment.provider_payment_id = payment.provider_payment_id or provider_id or None
        payment.recurring_profile_id = (
            str(form.get("pg_recurring_profile_id") or "") or payment.recurring_profile_id
        )
        payment.callback = snapshot
        # Приход — в журнал; повтор колбэка упрётся в ключ идемпотентности.
        await self._ledger.post(
            payment.clinic_id,
            kind=KIND_CREDIT,
            source=SOURCE_PROVIDER,
            amount_tiyin=payment.amount_tiyin,
            description=payment.description,
            payment_id=payment.id,
            provider_payload=snapshot,
            idempotency_key=f"freedompay:{payment.provider_payment_id or payment.id}",
            created_by=payment.created_by,
        )
        if payment.recurring_profile_id:
            wallet = await self._ledger.wallet(payment.clinic_id)
            wallet.recurring_profile_id = payment.recurring_profile_id
        if payment.purpose == PURPOSE_SUBSCRIPTION:
            # Списание за подписку + открытие периода (коммитит продление).
            entry, _ = await self._ledger.charge_subscription(
                payment.clinic_id,
                payment.months,
                manage=self._manage,
                payment_id=payment.id,
                created_by=payment.created_by,
                reason="freedompay",
            )
            payment.period_start, payment.period_end = entry.period_start, entry.period_end
            await self._session.commit()
        else:
            await self._session.commit()
            # Пополнение: если подписка ждёт денег — продлить сразу, не ночью.
            await self._ledger.try_autorenew(
                payment.clinic_id, manage=self._manage, reason="autopay_balance"
            )
        logger.info(
            "freedompay_payment_paid",
            order_id=str(payment.id),
            clinic_id=str(payment.clinic_id),
            purpose=payment.purpose,
        )
        return ResultReply("ok", "Оплата учтена")
