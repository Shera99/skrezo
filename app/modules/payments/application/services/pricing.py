"""Цена подписки по действующему тарифу — одна для оплаты, баланса и автопродления.

Спецплан Skrezo (``clinic_custom_plan``), если он есть, заменяет пакеты целиком:
цена и места — из него, и это видят оплата, списание с баланса и автопродление.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.payments.domain.periods import MAX_MONTHS, MIN_MONTHS
from app.modules.payments.infrastructure.repositories.plan_repository import PlanRepository
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.modules.tariffs.domain.pricing import PackageDef, compute_clinic_price, package_for
from app.modules.tariffs.infrastructure.repositories.tariff_repository import TariffRepository
from app.shared.db.enums import ClinicType
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.tariff_version import TariffVersion


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """Цена подписки клиники: в месяц и за выбранный срок."""

    monthly_tiyin: int
    months: int
    seats: int | None

    @property
    def total_tiyin(self) -> int:
        return self.monthly_tiyin * self.months


@dataclass(frozen=True, slots=True)
class CustomPlanTerms:
    """Спецплан клиники так, как его видит кабинет."""

    title: str
    seats: int
    monthly_tiyin: int


@dataclass(frozen=True, slots=True)
class TariffCard:
    """Тариф клиники для кабинета: из чего складывается её цена."""

    clinic_type: ClinicType
    #: Пакеты по возрастанию мест; у соло — пусто (цена фиксированная).
    packages: list[PackageDef]
    #: Доплата за место сверх последнего пакета («Сеть»); у соло — ``None``.
    over_price_tiyin: int | None
    #: Мест, по которым считается цена; у соло — ``None``.
    seats: int | None
    monthly_tiyin: int
    #: Пакет, в который попадает клиника; ``None`` — соло или мест больше пакетов.
    current_package: str | None
    #: Спецплан Skrezo: заполнен — цена и места из него, пакеты только для справки.
    custom_plan: CustomPlanTerms | None = None


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
        card = await self.card(clinic_id)
        return PriceQuote(monthly_tiyin=card.monthly_tiyin, months=months, seats=card.seats)

    async def card(self, clinic_id: uuid.UUID) -> TariffCard:
        """Тариф клиники целиком: пакеты, доплата за место и текущая цена.

        Считается тем же правилом, что и цена оплаты: мест — лимит по тарифу
        либо занятые, если занято больше. 409 ``tariff_not_configured`` —
        тариф для типа клиники ещё не заведён.
        """
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        version = await self._tariffs.get_current(clinic.type)
        custom = await PlanRepository(self._session).custom_plan(clinic_id)
        if custom is not None:
            # Спецплан не зависит от того, настроены ли публичные пакеты.
            return TariffCard(
                clinic_type=clinic.type,
                packages=await self._packages(version) if version is not None else [],
                over_price_tiyin=(
                    int(version.over_price_tiyin)
                    if version is not None and version.over_price_tiyin
                    else None
                ),
                seats=custom.seats,
                monthly_tiyin=custom.price_tiyin,
                current_package=None,
                custom_plan=CustomPlanTerms(
                    title=custom.title, seats=custom.seats, monthly_tiyin=custom.price_tiyin
                ),
            )
        not_configured = ConflictError(
            "Тариф ещё не настроен — напишите в Skrezo", code="tariff_not_configured"
        )
        if version is None:
            raise not_configured
        if clinic.type is ClinicType.SOLO:
            if not version.price_tiyin:
                raise not_configured
            return TariffCard(
                clinic_type=clinic.type,
                packages=[],
                over_price_tiyin=None,
                seats=None,
                monthly_tiyin=int(version.price_tiyin),
                current_package=None,
            )
        packages = await self._packages(version)
        if not packages or not version.over_price_tiyin:
            raise not_configured
        used, limit = await self._subscriptions.seats(clinic_id)
        seats = max(limit or 0, used, 1)
        current = package_for(packages, seats)
        return TariffCard(
            clinic_type=clinic.type,
            packages=packages,
            over_price_tiyin=int(version.over_price_tiyin),
            seats=seats,
            monthly_tiyin=compute_clinic_price(packages, int(version.over_price_tiyin), seats),
            current_package=current.name if current else None,
        )

    async def _packages(self, version: TariffVersion) -> list[PackageDef]:
        """Пакеты версии тарифа по возрастанию мест."""
        return sorted(
            (
                PackageDef(name=p.name, up_to=p.up_to, price=p.price_tiyin)
                for p in await self._tariffs.list_packages(version.id)
            ),
            key=lambda p: p.up_to,
        )
