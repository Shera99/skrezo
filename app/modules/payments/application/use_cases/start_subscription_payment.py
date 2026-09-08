"""Начать оплату подписки: цена по тарифу → платёж → страница FreedomPay.

Провайдер пока без автосписания, поэтому оплата — явный шаг владельца:
выбрал месяцы, ушёл на страницу оплаты, вернулся. Строка платежа создаётся
до перехода; период откроет только серверный колбэк с подписью.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, UpstreamError, ValidationAppError
from app.modules.payments.domain.periods import CURRENCY, MAX_MONTHS, MIN_MONTHS, som_amount
from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.modules.tariffs.domain.pricing import PackageDef, compute_clinic_price
from app.modules.tariffs.infrastructure.repositories.tariff_repository import TariffRepository
from app.shared.db.enums import ClinicType
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.platform_payment import PlatformPayment
from app.shared.freedompay import FreedomPayClient, FreedomPayError, InitPaymentRequest

_DIGITS = re.compile(r"\D+")


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """Цена подписки клиники: в месяц и за выбранный срок."""

    monthly_tiyin: int
    months: int
    seats: int | None

    @property
    def total_tiyin(self) -> int:
        return self.monthly_tiyin * self.months


class StartSubscriptionPaymentUseCase:
    def __init__(
        self,
        session: AsyncSession,
        payments: PlatformPaymentRepository,
        subscriptions: SubscriptionRepository,
        tariffs: TariffRepository,
        provider: FreedomPayClient | None,
    ) -> None:
        self._session = session
        self._payments = payments
        self._subscriptions = subscriptions
        self._tariffs = tariffs
        self._provider = provider

    @property
    def available(self) -> bool:
        """Подключена ли онлайн-оплата (реквизиты провайдера и публичный адрес)."""
        return self._provider is not None and bool(settings.public_api_url)

    async def quote(self, clinic_id: uuid.UUID, months: int) -> PriceQuote:
        """Цена по действующему тарифу: соло — фикс, клиника — пакет по местам."""
        if not MIN_MONTHS <= months <= MAX_MONTHS:
            raise ValidationAppError(
                f"Оплатить можно от {MIN_MONTHS} до {MAX_MONTHS} месяцев",
                code="months_out_of_range",
            )
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        version = await self._tariffs.get_current(clinic.type)
        if version is None:
            raise ConflictError(
                "Тариф ещё не настроен — напишите в Skrezo", code="tariff_not_configured"
            )
        if clinic.type is ClinicType.SOLO:
            if not version.price_tiyin:
                raise ConflictError(
                    "Тариф ещё не настроен — напишите в Skrezo", code="tariff_not_configured"
                )
            return PriceQuote(monthly_tiyin=int(version.price_tiyin), months=months, seats=None)
        packages = [
            PackageDef(name=p.name, up_to=p.up_to, price=p.price_tiyin)
            for p in await self._tariffs.list_packages(version.id)
        ]
        if not packages or not version.over_price_tiyin:
            raise ConflictError(
                "Тариф ещё не настроен — напишите в Skrezo", code="tariff_not_configured"
            )
        used, limit = await self._subscriptions.seats(clinic_id)
        seats = max(limit or 0, used, 1)
        return PriceQuote(
            monthly_tiyin=compute_clinic_price(packages, int(version.over_price_tiyin), seats),
            months=months,
            seats=seats,
        )

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        months: int,
        actor_id: uuid.UUID,
    ) -> PlatformPayment:
        """Создать платёж и получить у провайдера адрес страницы оплаты."""
        if self._provider is None:
            raise ConflictError(
                "Онлайн-оплата пока не подключена — напишите в Skrezo",
                code="payments_not_configured",
            )
        if not settings.public_api_url:
            raise ConflictError(
                "Не задан публичный адрес API для колбэка", code="payments_not_configured"
            )
        quote = await self.quote(clinic_id, months)
        clinic = await self._session.get(Clinic, clinic_id)
        assert clinic is not None
        # Телефон плательщика — провайдер подставит его в форму; почты у аккаунтов нет.
        account = await self._session.get(Account, actor_id)
        contact_phone = account.phone if account is not None else None
        contact_email = None
        payment = PlatformPayment(
            clinic_id=clinic_id,
            provider="freedompay",
            amount_tiyin=quote.total_tiyin,
            currency=CURRENCY,
            months=months,
            description=f"Skrezo: подписка «{clinic.name}», {months} мес.",
            status="pending",
            created_by=actor_id,
        )
        self._session.add(payment)
        await self._session.flush()
        request = InitPaymentRequest(
            order_id=str(payment.id),
            amount=som_amount(payment.amount_tiyin),
            currency=CURRENCY,
            description=payment.description,
            result_url=f"{settings.public_api_url.rstrip('/')}/payments/freedompay/result",
            success_url=settings.freedompay_success_url or None,
            failure_url=settings.freedompay_failure_url or None,
            user_phone=_DIGITS.sub("", contact_phone or "") or None,
            user_email=contact_email or None,
            language="ru",
            testing_mode=settings.freedompay_testing_mode,
            lifetime_seconds=settings.freedompay_lifetime_seconds,
            extra={"pg_param1": str(clinic_id)},
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
        await self._session.commit()
        return payment
