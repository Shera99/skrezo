"""Метрики обучения ассистента (Ф6) — экран «Обучение → показатели»."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.access.presentation.deps import require_permission
from app.modules.ai.application.metrics import AssistantMetricsService
from app.modules.ai.presentation.deps import get_assistant_metrics_service
from app.modules.ai.presentation.schemas import (
    AssistantMetricsResponse,
    AutonomyMetricsOut,
    LearningMetricsOut,
    MetricsPeriodOut,
    MetricsSeriesPointOut,
    QualityMetricsOut,
    RegressionMetricsOut,
    RegressionRunSummaryOut,
    TrustGateOut,
    UsageMetricsOut,
)
from app.modules.reports.domain.period import ReportPeriod
from app.shared.auth import ClientContext
from app.shared.db.models.clinic import Clinic

router = APIRouter()


@router.get(
    "/clinic/assistant/metrics",
    response_model=AssistantMetricsResponse,
    summary="Метрики обучения ассистента и гейт доверия",
    response_description="Показатели за период, ряд по дням, готовность к следующей ступени",
)
async def assistant_metrics(
    period: ReportPeriod | None = Query(
        default=None, description="Пресет: `7d`, `1m` (по умолчанию), `3m`, `all`"
    ),
    date_from: date | None = Query(default=None, alias="dateFrom", description="Явный диапазон"),
    date_to: date | None = Query(default=None, alias="dateTo"),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: AssistantMetricsService = Depends(get_assistant_metrics_service),
    session: AsyncSession = Depends(get_session),
) -> AssistantMetricsResponse:
    """Учится ли бот: доля ответов без человека, доля правок черновиков, очередь
    «Обучение» и «выучил за период», итог последней регрессии, токены. Гейт
    доверия — по ТЗ: N одобренных ответов и ноль фактических ошибок (V01–V03)
    за неделю; отсюда берётся «153 из 200» для ступеней Ф7."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    clinic = await session.get(Clinic, context.clinic_id)
    if clinic is None:
        raise NotFoundError("Клиника не найдена", code="clinic_not_found")
    m = await service.collect(clinic, period=period, date_from=date_from, date_to=date_to)
    return AssistantMetricsResponse(
        period=MetricsPeriodOut(
            period=m.bounds.period.value if m.bounds.period else None,
            date_from=m.bounds.date_from,
            date_to=m.bounds.date_to,
            timezone=m.timezone,
        ),
        autonomy=AutonomyMetricsOut(**m.autonomy),
        quality=QualityMetricsOut(**m.quality),
        learning=LearningMetricsOut(**m.learning),
        regression=RegressionMetricsOut(
            cases_active=m.regression["cases_active"],
            cases_failing=m.regression["cases_failing"],
            last_run=(
                RegressionRunSummaryOut(**m.regression["last_run"])
                if m.regression["last_run"]
                else None
            ),
        ),
        usage=UsageMetricsOut(**m.usage),
        trust_gate=TrustGateOut(
            mode=m.mode,
            approved=m.gate.approved,
            required=m.gate.required,
            progress=m.gate.progress,
            fact_errors=m.gate.fact_errors,
            fact_error_days=m.gate.fact_error_days,
            ready=m.gate.ready,
        ),
        series=[MetricsSeriesPointOut(date=p.day, **p.values) for p in m.series],
    )
