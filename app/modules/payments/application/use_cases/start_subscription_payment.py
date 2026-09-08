"""Начать оплату у провайдера: подписка на N месяцев или пополнение баланса.

Провайдер пока без автосписания, поэтому оплата — явный шаг владельца:
выбрал сумму или срок, ушёл на страницу оплаты, вернулся. Строка платежа
создаётся до перехода; деньги проведёт по журналу только серверный колбэк.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, UpstreamError, ValidationAppError
from app.modules.payments.application.services.pricing import PriceQuote, SubscriptionPricing
from app.modules.payments.domain.ledger import PURPOSE_SUBSCRIPTION, PURPOSE_TOPUP
from app.modules.payments.domain.periods import CURRENCY, som_amount
from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.platform_payment import PlatformPayment
from app.shared.freedompay import FreedomPayClient, FreedomPayError, InitPaymentRequest

_DIGITS = re.compile(r"\D+")


class StartSubscriptionPaymentUseCase:
    def __init__(
        self,
        session: AsyncSession,
        payments: PlatformPaymentRepository,
        pricing: SubscriptionPricing,
        provider: FreedomPayClient | None,
    ) -> None:
        self._session = session
        self._payments = payments
        self._pricing = pricing
        self._provider = provider

    @property
    def available(self) -> bool:
        """Подключена ли онлайн-оплата (реквизиты провайдера и публичный адрес)."""
        return self._provider is not None and bool(settings.public_api_url)

    async def quote(self, clinic_id: uuid.UUID, months: int) -> PriceQuote:
        return await self._pricing.quote(clinic_id, months)

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
        months: int | None,
        topup_tiyin: int | None,
    ) -> PlatformPayment:
        """Создать платёж и получить у провайдера адрес страницы оплаты.

        ``months`` — оплата подписки (сумма по тарифу), ``topup_tiyin`` —
        пополнение баланса на произвольную сумму (не меньше минимума).
        """
        if not self.available:
            raise ConflictError(
                "Онлайн-оплата пока не подключена — напишите в Skrezo",
                code="payments_not_configured",
            )
        assert self._provider is not None
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is None:
            raise ConflictError("Клиника не найдена", code="clinic_not_found")
        if months is not None:
            quote = await self._pricing.quote(clinic_id, months)
            amount, purpose = quote.total_tiyin, PURPOSE_SUBSCRIPTION
            description = f"Skrezo: подписка «{clinic.name}», {months} мес."
        elif topup_tiyin is not None:
            if topup_tiyin < settings.topup_min_tiyin:
                raise ValidationAppError(
                    f"Минимальное пополнение — {settings.topup_min_tiyin // 100} сом",
                    code="topup_too_small",
                )
            amount, purpose, months = topup_tiyin, PURPOSE_TOPUP, 0
            description = f"Skrezo: пополнение баланса «{clinic.name}»"
        else:
            raise ValidationAppError("Укажите months или amountTiyin", code="payment_request_empty")

        payment = PlatformPayment(
            clinic_id=clinic_id,
            provider="freedompay",
            purpose=purpose,
            amount_tiyin=amount,
            currency=CURRENCY,
            months=months,
            description=description,
            status="pending",
            created_by=actor_id,
        )
        self._session.add(payment)
        await self._session.flush()
        # Телефон плательщика — провайдер подставит его в форму; почты у аккаунтов нет.
        account = await self._session.get(Account, actor_id)
        request = InitPaymentRequest(
            order_id=str(payment.id),
            amount=som_amount(payment.amount_tiyin),
            currency=CURRENCY,
            description=payment.description,
            result_url=f"{settings.public_api_url.rstrip('/')}/payments/freedompay/result",
            success_url=settings.freedompay_success_url or None,
            failure_url=settings.freedompay_failure_url or None,
            user_phone=_DIGITS.sub("", account.phone if account else "") or None,
            user_email=None,
            language="ru",
            testing_mode=settings.freedompay_testing_mode,
            lifetime_seconds=settings.freedompay_lifetime_seconds,
            extra={"pg_param1": str(clinic_id), "pg_param2": purpose},
        )
        try:
            result = await self._provider.init_payment(request)
        except FreedomPayError as exc:
            payment.status = "failed"
            payment.failure_description = str(exc)[:500]
            await self._session.commit()
            raise UpstreamError(
                "Платёжный сервис не принял запрос — попробуйте позже", code="payment_init_failed"
            ) from exc
        payment.provider_payment_id = result.payment_id
        payment.redirect_url = result.redirect_url
        payment.provider_response = dict(result.raw)
        await self._session.commit()
        return payment
