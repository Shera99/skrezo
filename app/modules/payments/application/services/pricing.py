"""Цена подписки по действующему тарифу — одна для оплаты, баланса и автопродления."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.payments.domain.periods import MAX_MONTHS, MIN_MONTHS
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.modules.tariffs.domain.pricing import PackageDef, compute_clinic_price
from app.modules.tariffs.infrastructure.repositories.tariff_repository import TariffRepository
from app.shared.db.enums import ClinicType
from app.shared.db.models.clinic import Clinic


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """Цена подписки клиники: в месяц и за выбранный срок."""

    monthly_tiyin: int
    months: int
    seats: int | None

    @property
    def total_tiyin(self) -> int:
        return self.monthly_tiyin * self.months


class SubscriptionPricing:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tariffs = TariffRepository(session)
        self._subscriptions = SubscriptionRepository(session)

    async def quote(self, clinic_id: uuid.UUID, months: int) -> PriceQuote:
        """Соло — фикс, клиника — пакет по местам (лимит по тарифу либо занятые)."""
        if not MIN_MONTHS <= months <= MAX_MONTHS:
            raise ValidationAppError(
                f"Оплатить можно от {MIN_MONTHS} до {MAX_MONTHS} месяцев",
                code="months_out_of_range",
            )
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        version = await self._tariffs.get_current(clinic.type)
        not_configured = ConflictError(
            "Тариф ещё не настроен — напишите в Skrezo", code="tariff_not_configured"
        )
        if version is None:
            raise not_configured
        if clinic.type is ClinicType.SOLO:
            if not version.price_tiyin:
                raise not_configured
            return PriceQuote(monthly_tiyin=int(version.price_tiyin), months=months, seats=None)
        packages = [
            PackageDef(name=p.name, up_to=p.up_to, price=p.price_tiyin)
            for p in await self._tariffs.list_packages(version.id)
        ]
        if not packages or not version.over_price_tiyin:
            raise not_configured
        used, limit = await self._subscriptions.seats(clinic_id)
        seats = max(limit or 0, used, 1)
        return PriceQuote(
            monthly_tiyin=compute_clinic_price(packages, int(version.over_price_tiyin), seats),
            months=months,
            seats=seats,
        )
