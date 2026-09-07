"""Агрегаты для метрик обучения: сообщения бота, эскалации, пробелы, база, ряды по дням."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.modules.ai.domain.metrics import BLOCK_CODES, FACT_ERROR_CODES, NEEDS_HUMAN_CODE
from app.shared.db.enums import AiMessageRole, GlossaryStatus
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_knowledge_gap import AiKnowledgeGap
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_regression_run import AiRegressionRun
from app.shared.db.models.clinic_usage_day import ClinicUsageDay
from app.shared.db.models.glossary_term import GlossaryTerm
from app.shared.db.models.kb_document import KbDocument

_SENT_DRAFT = ("approved", "edited")


def _between(column: Any, start: datetime | None, end: datetime) -> ColumnElement[bool]:
    conditions = [column < end]
    if start is not None:
        conditions.insert(0, column >= start)
    return and_(*conditions)


class MetricsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Сообщения бота ──────────────────────────────────────────────────────

    async def bot_messages(
        self, clinic_id: uuid.UUID, start: datetime | None, end: datetime
    ) -> dict[str, int]:
        """Счётчики ответов бота за период одним запросом (count … FILTER)."""
        passed = AiMessage.validator["passed"].as_boolean()
        codes = AiMessage.validator["codes"]
        fact_error = codes.has_any(array(list(FACT_ERROR_CODES)))
        blocked = and_(passed.is_(False), codes.has_any(array(list(BLOCK_CODES))))
        needs_human = and_(passed.is_(False), codes.has_any(array([NEEDS_HUMAN_CODE])))
        row = (
            await self._session.execute(
                select(
                    func.count()
                    .filter(AiMessage.sent_at.is_not(None), AiMessage.review.is_(None))
                    .label("replies_auto"),
                    func.count()
                    .filter(
                        AiMessage.sent_at.is_(None), AiMessage.review.is_(None), passed.is_(True)
                    )
                    .label("replies_shadow"),
                    func.count().filter(AiMessage.review.in_(_SENT_DRAFT)).label("drafts_sent"),
                    func.count().filter(AiMessage.review == "edited").label("drafts_edited"),
                    func.count().filter(AiMessage.review == "discarded").label("drafts_discarded"),
                    func.count().filter(AiMessage.review == "pending").label("drafts_pending"),
                    func.count().filter(blocked).label("validator_blocked"),
                    func.count().filter(needs_human).label("needs_human"),
                    func.count().filter(fact_error).label("fact_errors"),
                    func.count().filter(AiMessage.confidence == "low").label("low_confidence"),
                ).where(
                    AiMessage.clinic_id == clinic_id,
                    AiMessage.role == AiMessageRole.BOT,
                    _between(AiMessage.created_at, start, end),
                )
            )
        ).one()
        return {key: int(value or 0) for key, value in row._mapping.items()}

    async def approved_total(self, clinic_id: uuid.UUID) -> int:
        """Одобренных за всё время — счётчик гейта «153 из 200»."""
        return int(
            (
                await self._session.execute(
                    select(func.count()).where(
                        AiMessage.clinic_id == clinic_id,
                        AiMessage.role == AiMessageRole.BOT,
                        AiMessage.review.in_(_SENT_DRAFT),
                    )
                )
            ).scalar_one()
        )

    async def fact_errors_since(self, clinic_id: uuid.UUID, since: datetime) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.count()).where(
                        AiMessage.clinic_id == clinic_id,
                        AiMessage.role == AiMessageRole.BOT,
                        AiMessage.created_at >= since,
                        AiMessage.validator["codes"].has_any(array(list(FACT_ERROR_CODES))),
                    )
                )
            ).scalar_one()
        )

    async def dialogs(self, clinic_id: uuid.UUID, start: datetime | None, end: datetime) -> int:
        """Диалоги, в которых пациент писал за период."""
        return int(
            (
                await self._session.execute(
                    select(func.count(func.distinct(AiMessage.conversation_id))).where(
                        AiMessage.clinic_id == clinic_id,
                        AiMessage.role == AiMessageRole.PATIENT,
                        _between(AiMessage.created_at, start, end),
                    )
                )
            ).scalar_one()
        )

    # ── Эскалации ───────────────────────────────────────────────────────────

    async def escalations_by_reason(
        self, clinic_id: uuid.UUID, start: datetime | None, end: datetime
    ) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(AiEscalation.reason, func.count())
                .where(
                    AiEscalation.clinic_id == clinic_id,
                    _between(AiEscalation.created_at, start, end),
                )
                .group_by(AiEscalation.reason)
            )
        ).all()
        return {str(reason): int(count) for reason, count in rows}

    # ── Очередь «Обучение» и база знаний ────────────────────────────────────

    async def gaps(
        self, clinic_id: uuid.UUID, start: datetime | None, end: datetime
    ) -> dict[str, float]:
        answered = and_(
            AiKnowledgeGap.status == "answered", _between(AiKnowledgeGap.resolved_at, start, end)
        )
        hours = (
            func.extract("epoch", AiKnowledgeGap.resolved_at - AiKnowledgeGap.first_asked_at) / 3600
        )
        row = (
            await self._session.execute(
                select(
                    func.count().filter(AiKnowledgeGap.status == "unknown").label("open"),
                    func.count()
                    .filter(_between(AiKnowledgeGap.first_asked_at, start, end))
                    .label("opened"),
                    func.count().filter(answered).label("answered"),
                    func.count()
                    .filter(
                        AiKnowledgeGap.status == "dismissed",
                        _between(AiKnowledgeGap.resolved_at, start, end),
                    )
                    .label("dismissed"),
                    func.avg(hours).filter(answered).label("avg_hours_to_answer"),
                ).where(AiKnowledgeGap.clinic_id == clinic_id)
            )
        ).one()
        return {key: float(value or 0) for key, value in row._mapping.items()}

    async def kb(
        self, clinic_id: uuid.UUID, start: datetime | None, end: datetime
    ) -> dict[str, int]:
        row = (
            await self._session.execute(
                select(
                    func.count().filter(KbDocument.is_active.is_(True)).label("active"),
                    func.count().filter(_between(KbDocument.created_at, start, end)).label("added"),
                ).where(KbDocument.clinic_id == clinic_id)
            )
        ).one()
        return {key: int(value or 0) for key, value in row._mapping.items()}

    async def glossary(self, clinic_id: uuid.UUID) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(GlossaryTerm.status, func.count())
                .where(GlossaryTerm.clinic_id == clinic_id)
                .group_by(GlossaryTerm.status)
            )
        ).all()
        counts = {"pending": 0, "approved": 0}
        for status, count in rows:
            key = status.value if isinstance(status, GlossaryStatus) else str(status)
            counts[key] = int(count)
        return counts

    async def last_regression_run(self, clinic_id: uuid.UUID) -> AiRegressionRun | None:
        return (
            await self._session.execute(
                select(AiRegressionRun)
                .where(AiRegressionRun.clinic_id == clinic_id)
                .order_by(AiRegressionRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def usage(
        self, clinic_id: uuid.UUID, date_from: date | None, date_to: date
    ) -> dict[str, int]:
        conditions = [ClinicUsageDay.clinic_id == clinic_id, ClinicUsageDay.day <= date_to]
        if date_from is not None:
            conditions.append(ClinicUsageDay.day >= date_from)
        row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(ClinicUsageDay.model_calls), 0).label("model_calls"),
                    func.coalesce(func.sum(ClinicUsageDay.model_input_tokens), 0).label(
                        "input_tokens"
                    ),
                    func.coalesce(func.sum(ClinicUsageDay.model_output_tokens), 0).label(
                        "output_tokens"
                    ),
                ).where(*conditions)
            )
        ).one()
        return {key: int(value or 0) for key, value in row._mapping.items()}

    # ── Ряды по дням (в таймзоне клиники) ───────────────────────────────────

    async def series(
        self, clinic_id: uuid.UUID, tz: str, start: datetime, end: datetime
    ) -> dict[date, dict[str, int]]:
        """Точки по дням: ответы без человека, черновики, эскалации, пробелы."""
        points: dict[date, dict[str, int]] = {}

        def bump(day: Any, key: str, value: Any) -> None:
            if day is None:
                return
            points.setdefault(day, {})[key] = int(value or 0)

        def day_of(column: Any) -> Any:
            """Локальные сутки клиники: timestamptz → timestamp в её зоне → date."""
            return cast(func.timezone(tz, column), Date)

        bot_day = day_of(AiMessage.created_at)
        for day, auto, drafts in (
            await self._session.execute(
                select(
                    bot_day,
                    func.count().filter(AiMessage.sent_at.is_not(None), AiMessage.review.is_(None)),
                    func.count().filter(AiMessage.review.in_(_SENT_DRAFT)),
                )
                .where(
                    AiMessage.clinic_id == clinic_id,
                    AiMessage.role == AiMessageRole.BOT,
                    _between(AiMessage.created_at, start, end),
                )
                .group_by(bot_day)
            )
        ).all():
            bump(day, "replies_auto", auto)
            bump(day, "drafts_sent", drafts)

        esc_day = day_of(AiEscalation.created_at)
        for day, count in (
            await self._session.execute(
                select(esc_day, func.count())
                .where(
                    AiEscalation.clinic_id == clinic_id,
                    _between(AiEscalation.created_at, start, end),
                )
                .group_by(esc_day)
            )
        ).all():
            bump(day, "escalations", count)

        opened_day = day_of(AiKnowledgeGap.first_asked_at)
        for day, count in (
            await self._session.execute(
                select(opened_day, func.count())
                .where(
                    AiKnowledgeGap.clinic_id == clinic_id,
                    _between(AiKnowledgeGap.first_asked_at, start, end),
                )
                .group_by(opened_day)
            )
        ).all():
            bump(day, "gaps_opened", count)

        answered_day = day_of(AiKnowledgeGap.resolved_at)
        for day, count in (
            await self._session.execute(
                select(answered_day, func.count())
                .where(
                    AiKnowledgeGap.clinic_id == clinic_id,
                    AiKnowledgeGap.status == "answered",
                    _between(AiKnowledgeGap.resolved_at, start, end),
                )
                .group_by(answered_day)
            )
        ).all():
            bump(day, "gaps_answered", count)
        return points


def utc_now() -> datetime:
    return datetime.now(UTC)


def days_ago(days: int) -> datetime:
    return utc_now() - timedelta(days=days)
