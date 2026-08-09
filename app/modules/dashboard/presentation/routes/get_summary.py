"""Роут: GET /admin/dashboard/summary — метрики за период.

Принимает диапазон дат `from`/`to` (§5 спеки; на дашборде — пресеты day|week|
month, развёрнутые фронтом в диапазон). Метрики — текущий срез; период влияет
на подпись даты. Биллинг-зависимые блоки (MRR) — нули.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query

from app.modules.dashboard.application.use_cases.get_summary import GetSummaryUseCase
from app.modules.dashboard.presentation.deps import get_summary_use_case
from app.modules.dashboard.presentation.schemas import (
    ActiveBlock,
    CancelledBlock,
    ConversionBlock,
    DashboardSummary,
    MrrBlock,
    RisksBlock,
    TrialBlock,
)
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.enums import ClinicType, SubscriptionStatus

router = APIRouter()

_MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _date_label(day: date) -> str:
    return f"{day.day} {_MONTHS_RU[day.month - 1]} {day.year}"


@router.get(
    "/admin/dashboard/summary",
    response_model=DashboardSummary,
    summary="Сводка дашборда за период",
    response_description="Метрики: активные, MRR, триал, риски, конверсия, отмены",
)
async def dashboard_summary(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetSummaryUseCase = Depends(get_summary_use_case),
) -> DashboardSummary:
    data = await use_case.execute()

    active_total = data.active[ClinicType.SOLO] + data.active[ClinicType.CLINIC]
    cancelled_total = data.cancelled[ClinicType.SOLO] + data.cancelled[ClinicType.CLINIC]
    pct = round(data.converted / data.leads_total * 100, 1) if data.leads_total else 0.0
    label_day = date_to or datetime.now(UTC).date()

    return DashboardSummary(
        date_label=_date_label(label_day),
        active=ActiveBlock(
            total=active_total,
            solo=data.active[ClinicType.SOLO],
            clinic=data.active[ClinicType.CLINIC],
        ),
        mrr=MrrBlock(),
        trial=TrialBlock(
            total=data.trial_total,
            no_card=data.trial_total - data.trial_with_card,
            with_card=data.trial_with_card,
        ),
        risks=RisksBlock(
            trial_expired=data.status_counts[SubscriptionStatus.TRIAL_EXPIRED],
            awaiting_card=data.status_counts[SubscriptionStatus.AWAITING_CARD],
            past_due=data.status_counts[SubscriptionStatus.PAST_DUE],
            blocked=data.status_counts[SubscriptionStatus.BLOCKED],
        ),
        conversion=ConversionBlock(pct=pct, converted=data.converted, leads=data.leads_total),
        cancelled=CancelledBlock(
            total=cancelled_total,
            solo=data.cancelled[ClinicType.SOLO],
            clinic=data.cancelled[ClinicType.CLINIC],
        ),
    )
