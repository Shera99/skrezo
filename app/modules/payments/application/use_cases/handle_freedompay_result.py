"""Серверный колбэк FreedomPay (``result_url``): единственный, кто открывает период.

Подпись сверяется до всего остального; сумма — с нашей строкой платежа (не
с тем, что прислали). Повтор колбэка по уже оплаченному платежу — «ok»
без второго продления: провайдер шлёт результат, пока не получит ответ.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.payments.domain.periods import amount_matches, next_period
from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.enums import SubscriptionStatus
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
        subscriptions: SubscriptionRepository,
        manage: ManageSubscriptionUseCase,
    ) -> None:
        self._session = session
        self._payments = payments
        self._subscriptions = subscriptions
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
        subscription = await self._subscriptions.by_clinic(payment.clinic_id)
        current_end = (
            subscription.current_period_end
            if subscription is not None and subscription.status is SubscriptionStatus.ACTIVE
            else None
        )
        start, end = next_period(now, current_end, payment.months)
        payment.status = "paid"
        payment.paid_at = now
        payment.period_start = start
        payment.period_end = end
        payment.provider_payment_id = payment.provider_payment_id or provider_id or None
        payment.recurring_profile_id = (
            str(form.get("pg_recurring_profile_id") or "") or payment.recurring_profile_id
        )
        payment.callback = snapshot
        await self._session.flush()
        # Продление коммитит сам и сбрасывает кеш доступа: клиника пишет сразу.
        await self._manage.activate(
            payment.clinic_id, paid_until=end, period_start=start, reason="freedompay"
        )
        logger.info(
            "freedompay_payment_paid",
            order_id=str(payment.id),
            clinic_id=str(payment.clinic_id),
            until=end.isoformat(),
        )
        return ResultReply("ok", "Оплата учтена")
