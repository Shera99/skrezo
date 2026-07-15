"""Роут: GET /admin/dashboard/trends — ряды по месяцам под графики (§4.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.modules.dashboard.application.use_cases.get_trends import GetTrendsUseCase
from app.modules.dashboard.presentation.deps import get_trends_use_case
from app.modules.dashboard.presentation.schemas import (
    ActivePoint,
    DashboardTrends,
    LeadsPoint,
    MrrPoint,
    RatioBlock,
)
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()


@router.get(
    "/admin/dashboard/trends",
    response_model=DashboardTrends,
    summary="Тренды дашборда по месяцам",
    response_description="Ряды: MRR, активные (solo/clinic), заявки→клиенты, срез solo/clinic",
)
async def dashboard_trends(
    months: int = Query(default=6, ge=1, le=24, description="Сколько последних месяцев"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetTrendsUseCase = Depends(get_trends_use_case),
) -> DashboardTrends:
    data = await use_case.execute(months=months, now=datetime.now(UTC))
    return DashboardTrends(
        mrr=[MrrPoint(month=m, value=v) for m, v in zip(data.months, data.mrr, strict=True)],
        active_clients=[
            ActivePoint(month=m, solo=s, clinic=c)
            for m, s, c in zip(data.months, data.active_solo, data.active_clinic, strict=True)
        ],
        leads_to_clients=[
            LeadsPoint(month=m, leads=lead, clients=cl)
            for m, lead, cl in zip(data.months, data.leads, data.new_clients, strict=True)
        ],
        solo_clinic_ratio=RatioBlock(solo=data.ratio_solo, clinic=data.ratio_clinic),
    )
