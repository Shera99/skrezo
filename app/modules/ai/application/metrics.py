"""Метрики обучения ассистента (Ф6): сбор по клинике за период.

Один экран отвечает на вопрос «учится ли бот»: доля ответов без человека,
доля правок черновиков, очередь «Обучение», сколько выучил, что говорит
регрессия и сколько это стоило в токенах. Гейт доверия (Ф7) считается здесь
же — иначе «153 из 200» неоткуда взять.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.ai.domain.metrics import TrustGate, share, trust_gate
from app.modules.ai.infrastructure.repositories.metrics_repository import (
    MetricsRepository,
    days_ago,
)
from app.modules.ai.infrastructure.repositories.regression_repository import RegressionRepository
from app.modules.reports.domain.period import PeriodBounds, ReportPeriod, resolve_period, resolve_tz
from app.shared.db.models.ai_regression_run import AiRegressionRun
from app.shared.db.models.clinic import Clinic

#: Для «за всё время» ряд по дням ограничен — иначе график на годы.
_SERIES_MAX_DAYS = 92
_SERIES_KEYS = ("replies_auto", "drafts_sent", "escalations", "gaps_opened", "gaps_answered")


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    day: date
    values: dict[str, int]


@dataclass(frozen=True, slots=True)
class AssistantMetrics:
    bounds: PeriodBounds
    timezone: str
    mode: str
    autonomy: dict[str, Any]
    quality: dict[str, Any]
    learning: dict[str, Any]
    regression: dict[str, Any]
    usage: dict[str, int]
    gate: TrustGate
    series: list[SeriesPoint] = field(default_factory=list)


class AssistantMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = MetricsRepository(session)

    async def collect(
        self,
        clinic: Clinic,
        *,
        period: ReportPeriod | None,
        date_from: date | None,
        date_to: date | None,
    ) -> AssistantMetrics:
        tz = resolve_tz(clinic.timezone)
        bounds = resolve_period(period, date_from, date_to, tz)
        start, end = bounds.start, bounds.end
        clinic_id: uuid.UUID = clinic.id

        bot = await self._repository.bot_messages(clinic_id, start, end)
        escalations = await self._repository.escalations_by_reason(clinic_id, start, end)
        escalations_total = sum(escalations.values())
        dialogs = await self._repository.dialogs(clinic_id, start, end)
        gaps = await self._repository.gaps(clinic_id, start, end)
        kb = await self._repository.kb(clinic_id, start, end)
        glossary = await self._repository.glossary(clinic_id)
        last_run = await self._repository.last_regression_run(clinic_id)
        cases = await RegressionRepository(self._session).case_counts(clinic_id)
        usage = await self._repository.usage(clinic_id, bounds.date_from, bounds.date_to)

        gate = trust_gate(
            await self._repository.approved_total(clinic_id),
            required=settings.ai_trust_gate_approvals,
            fact_errors=await self._repository.fact_errors_since(
                clinic_id, days_ago(settings.ai_trust_gate_fact_error_days)
            ),
            fact_error_days=settings.ai_trust_gate_fact_error_days,
        )

        # Знаменатель «доли без человека» — всё, что дошло до пациента или до
        # человека: авто-ответы, отправленные черновики, эскалации.
        handled = bot["replies_auto"] + bot["drafts_sent"] + escalations_total
        autonomy = {
            "dialogs": dialogs,
            "replies_auto": bot["replies_auto"],
            "replies_shadow": bot["replies_shadow"],
            "drafts_sent": bot["drafts_sent"],
            "drafts_edited": bot["drafts_edited"],
            "drafts_discarded": bot["drafts_discarded"],
            "drafts_pending": bot["drafts_pending"],
            "escalations": escalations_total,
            "autonomy_share": share(bot["replies_auto"], handled),
            "edit_share": share(bot["drafts_edited"], bot["drafts_sent"]),
        }
        quality = {
            "validator_blocked": bot["validator_blocked"],
            "needs_human": bot["needs_human"],
            "fact_errors": bot["fact_errors"],
            "low_confidence": bot["low_confidence"],
            "medical_urgent": escalations.get("urgent_medical", 0),
            "escalations_by_reason": escalations,
        }
        learning = {
            "gaps_open": int(gaps["open"]),
            "gaps_opened": int(gaps["opened"]),
            "gaps_answered": int(gaps["answered"]),
            "gaps_dismissed": int(gaps["dismissed"]),
            "avg_hours_to_answer": round(gaps["avg_hours_to_answer"], 1),
            "kb_documents_active": kb["active"],
            "kb_documents_added": kb["added"],
            "glossary_pending": glossary["pending"],
            "glossary_approved": glossary["approved"],
        }
        regression = {
            "cases_active": cases["active"],
            "cases_failing": cases["failing"],
            "last_run": _run_summary(last_run),
        }
        series_start = start or max(
            bounds.end - timedelta(days=_SERIES_MAX_DAYS),
            datetime.min.replace(tzinfo=bounds.end.tzinfo),
        )
        points = await self._repository.series(clinic_id, clinic.timezone, series_start, end)
        series = [
            SeriesPoint(day, {key: values.get(key, 0) for key in _SERIES_KEYS})
            for day, values in sorted(points.items())
        ]
        return AssistantMetrics(
            bounds=bounds,
            timezone=clinic.timezone,
            mode=clinic.ai_mode.value,
            autonomy=autonomy,
            quality=quality,
            learning=learning,
            regression=regression,
            usage=usage,
            gate=gate,
            series=series,
        )


def _run_summary(run: AiRegressionRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "mode": run.mode,
        "status": run.status,
        "passed": run.passed,
        "failed": run.failed,
        "skipped": run.skipped,
        "finished_at": run.finished_at,
    }
