"""Use-case'ы чтения использования: по клинике за период и по платформе за месяц."""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import date

from app.core.config import settings
from app.modules.usage.domain.rates import RateCard, UsageTotals, estimate_cost_tiyin
from app.modules.usage.infrastructure.repositories.usage_repository import UsageRepository
from app.shared.db.models.clinic_usage_day import USAGE_COUNTERS, ClinicUsageDay


def rate_card_from_settings() -> RateCard:
    """Прайс-лист из env: нули означают «сметы нет, только счётчики»."""
    return RateCard(
        ai_input_per_mtok=settings.ai_rate_input_tiyin_per_mtok,
        ai_output_per_mtok=settings.ai_rate_output_tiyin_per_mtok,
        ai_cached_input_per_mtok=settings.ai_rate_cached_input_tiyin_per_mtok,
        wa_utility=settings.wa_rate_utility_tiyin,
        wa_marketing=settings.wa_rate_marketing_tiyin,
        wa_authentication=settings.wa_rate_authentication_tiyin,
    )


def month_bounds(period: str) -> tuple[date, date]:
    """``YYYY-MM`` → первый и последний день месяца."""
    year, month = (int(part) for part in period.split("-"))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def totals_of(row: ClinicUsageDay) -> UsageTotals:
    return UsageTotals(**{name: int(getattr(row, name)) for name in USAGE_COUNTERS})


@dataclass(frozen=True, slots=True)
class ClinicUsage:
    clinic_id: uuid.UUID
    clinic_name: str
    totals: UsageTotals
    estimated_cost_tiyin: int


class GetUsageUseCase:
    def __init__(self, repository: UsageRepository) -> None:
        self._repository = repository

    async def by_clinic(
        self, clinic_id: uuid.UUID, date_from: date, date_to: date
    ) -> tuple[list[ClinicUsageDay], UsageTotals, int]:
        rows = await self._repository.daily(clinic_id, date_from, date_to)
        totals = UsageTotals()
        for row in rows:
            totals = totals + totals_of(row)
        return rows, totals, estimate_cost_tiyin(totals, rate_card_from_settings())

    async def by_platform(self, period: str) -> tuple[list[ClinicUsage], UsageTotals, int]:
        date_from, date_to = month_bounds(period)
        rates = rate_card_from_settings()
        clinics = [
            ClinicUsage(clinic_id, name, totals, estimate_cost_tiyin(totals, rates))
            for clinic_id, name, totals in await self._repository.totals_by_clinic(
                date_from, date_to
            )
        ]
        grand = UsageTotals()
        for item in clinics:
            grand = grand + item.totals
        return clinics, grand, estimate_cost_tiyin(grand, rates)
