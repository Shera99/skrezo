"""Начать оплату у провайдера: подписка на N месяцев или пополнение баланса.

Провайдер пока без автосписания, поэтому оплата — явный шаг владельца:
выбрал сумму или срок, ушёл на страницу оплаты, вернулся. Строка платежа
создаётся до перехода; деньги проведёт по журналу только серверный колбэк.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, UpstreamError, ValidationAppError
from app.modules.payments.application.services.ledger_service import ensure_renewable
from app.modules.payments.application.services.pricing import PriceQuote, SubscriptionPricing
from app.modules.payments.domain.checkout import (
    METHOD_AUTO,
    METHOD_BUTTON,
    ReturnMode,
    return_method,
)
from app.modules.payments.domain.ledger import PURPOSE_SUBSCRIPTION, PURPOSE_TOPUP
from app.modules.payments.domain.periods import CURRENCY, som_amount
from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.cabinet_links import pay_url
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.platform_payment import PlatformPayment
from app.shared.freedompay import (
    FreedomPayClient,
    FreedomPayError,
    FreedomPayRejectedError,
    InitPaymentRequest,
    InitPaymentResult,
)

logger = structlog.get_logger(__name__)

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
        return_mode: ReturnMode | None = None,
    ) -> PlatformPayment:
        """Создать платёж и получить у провайдера адрес страницы оплаты.

        ``months`` — оплата подписки (сумма по тарифу), ``topup_tiyin`` —
        пополнение баланса на произвольную сумму (не меньше минимума).
        ``return_mode`` — как вернуть человека после оплаты: ``auto`` — сразу
        (компьютер), ``button`` или ничего — кнопкой «Вернуться» (телефон).
        Возврат — на ``{CABINET_URL}/pay?payment=<id>``: этот путь всегда
        открывается в браузере, где человек и платил, и показывает итог.
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
            # Ручной блок и уход клиента оплатой не снимаются — 409 до создания
            # платежа, чтобы клиника не заплатила за то, чего не получит.
            # Пополнение баланса можно: деньги лягут на баланс, блок останется.
            ensure_renewable(await SubscriptionRepository(self._session).by_clinic(clinic_id))
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
        # Возврат — одна страница и для успеха, и для отказа: она спрашивает
        # итог по id. Адрес кабинета не настроен — возврат не передаём вовсе.
        back_url = pay_url(payment.id)
        method = (
            return_method(return_mode, auto_enabled=settings.freedompay_auto_return)
            if back_url
            else None
        )
        request = InitPaymentRequest(
            order_id=str(payment.id),
            amount=som_amount(payment.amount_tiyin),
            currency=CURRENCY,
            description=payment.description,
            result_url=f"{settings.public_api_url.rstrip('/')}/payments/freedompay/result",
            success_url=back_url,
            failure_url=back_url,
            user_phone=_DIGITS.sub("", account.phone if account else "") or None,
            user_email=None,
            language="ru",
            testing_mode=settings.freedompay_testing_mode,
            lifetime_seconds=settings.freedompay_lifetime_seconds,
            extra={"pg_param1": str(clinic_id), "pg_param2": purpose},
            success_url_method=method,
            failure_url_method=method,
        )
        try:
            result = await self._init(request)
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

    async def _init(self, request: InitPaymentRequest) -> InitPaymentResult:
        """Запрос к провайдеру; отказ на автоматическом возврате — повтор с кнопкой.

        ``AUTOGET`` нынешняя документация FreedomPay не перечисляет (он из API
        PayBox). Оплата не должна ломаться из-за удобства возврата: при явном
        отказе провайдера запрос повторяется один раз с кнопкой «Вернуться».
        Остальные ошибки (сеть, подпись ответа) не повторяются — платёж у
        провайдера мог родиться, второй с тем же номером заказа делать нельзя.
        """
        assert self._provider is not None
        try:
            return await self._provider.init_payment(request)
        except FreedomPayRejectedError as exc:
            if request.success_url_method != METHOD_AUTO:
                raise
            logger.warning(
                "freedompay_auto_return_rejected",
                order_id=request.order_id,
                code=exc.code,
                description=exc.description,
            )
            return await self._provider.init_payment(
                replace(request, success_url_method=METHOD_BUTTON, failure_url_method=METHOD_BUTTON)
            )
