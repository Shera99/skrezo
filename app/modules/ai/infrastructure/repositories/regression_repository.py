"""Репозиторий регрессии клиники: кейсы (с upsert по вопросу), прогоны, итоги."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.ai_regression_case import AiRegressionCase
from app.shared.db.models.ai_regression_result import AiRegressionResult
from app.shared.db.models.ai_regression_run import AiRegressionRun


class RegressionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Кейсы ───────────────────────────────────────────────────────────────

    async def upsert_case(
        self,
        clinic_id: uuid.UUID,
        *,
        kind: str,
        lang: str,
        question: str,
        normalized: str,
        expect: dict[str, Any],
        source: str,
        origin: dict[str, Any],
        note: str | None,
        created_by: uuid.UUID | None,
    ) -> uuid.UUID:
        """Тот же вопрос того же вида — обновить ожидание, не плодить дубль.

        Активность при повторе не трогаем: выключенный администратором кейс
        не должен воскресать от очередного одобренного ответа.
        """
        stmt = pg_insert(AiRegressionCase).values(
            id=uuid.uuid4(),
            clinic_id=clinic_id,
            kind=kind,
            lang=lang,
            question=question,
            normalized=normalized,
            expect=expect,
            source=source,
            origin=origin,
            note=note,
            created_by=created_by,
        )
        upsert = stmt.on_conflict_do_update(
            constraint="uq_ai_regression_case_question",
            set_={
                "expect": stmt.excluded.expect,
                "source": stmt.excluded.source,
                "origin": stmt.excluded.origin,
                "updated_at": func.now(),
            },
        ).returning(AiRegressionCase.id)
        return uuid.UUID(str((await self._session.execute(upsert)).scalar_one()))

    async def get_case(self, clinic_id: uuid.UUID, case_id: uuid.UUID) -> AiRegressionCase | None:
        return (
            await self._session.execute(
                select(AiRegressionCase).where(
                    AiRegressionCase.id == case_id, AiRegressionCase.clinic_id == clinic_id
                )
            )
        ).scalar_one_or_none()

    async def list_cases(
        self,
        clinic_id: uuid.UUID,
        *,
        kind: str | None,
        active: bool | None,
        result: str | None,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AiRegressionCase], int]:
        stmt = select(AiRegressionCase).where(AiRegressionCase.clinic_id == clinic_id)
        if kind:
            stmt = stmt.where(AiRegressionCase.kind == kind)
        if active is not None:
            stmt = stmt.where(AiRegressionCase.is_active.is_(active))
        if result:
            stmt = stmt.where(AiRegressionCase.last_result == result)
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                or_(AiRegressionCase.question.ilike(pattern), AiRegressionCase.note.ilike(pattern))
            )
        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                stmt.order_by(AiRegressionCase.created_at.desc(), AiRegressionCase.id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
        return list(rows), int(total)

    async def active_cases(self, clinic_id: uuid.UUID, *, limit: int) -> list[AiRegressionCase]:
        """Кейсы прогона — старые первыми: стартовый набор важнее свежих."""
        rows = (
            await self._session.execute(
                select(AiRegressionCase)
                .where(
                    AiRegressionCase.clinic_id == clinic_id,
                    AiRegressionCase.is_active.is_(True),
                )
                .order_by(AiRegressionCase.created_at, AiRegressionCase.id)
                .limit(limit)
            )
        ).scalars()
        return list(rows)

    async def case_counts(self, clinic_id: uuid.UUID) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(AiRegressionCase.is_active, AiRegressionCase.last_result, func.count())
                .where(AiRegressionCase.clinic_id == clinic_id)
                .group_by(AiRegressionCase.is_active, AiRegressionCase.last_result)
            )
        ).all()
        counts = {"total": 0, "active": 0, "failing": 0}
        for is_active, last_result, count in rows:
            counts["total"] += int(count)
            if is_active:
                counts["active"] += int(count)
                if last_result == "failed":
                    counts["failing"] += int(count)
        return counts

    # ── Прогоны ─────────────────────────────────────────────────────────────

    async def get_run(self, clinic_id: uuid.UUID, run_id: uuid.UUID) -> AiRegressionRun | None:
        return (
            await self._session.execute(
                select(AiRegressionRun).where(
                    AiRegressionRun.id == run_id, AiRegressionRun.clinic_id == clinic_id
                )
            )
        ).scalar_one_or_none()

    async def active_run(
        self, clinic_id: uuid.UUID, *, within_seconds: int
    ) -> AiRegressionRun | None:
        """Незавершённый прогон моложе таймаута задания — второй не ставим."""
        since = datetime.now(UTC) - timedelta(seconds=within_seconds)
        return (
            await self._session.execute(
                select(AiRegressionRun)
                .where(
                    AiRegressionRun.clinic_id == clinic_id,
                    AiRegressionRun.status.in_(("queued", "running")),
                    AiRegressionRun.created_at >= since,
                )
                .order_by(AiRegressionRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def list_runs(
        self, clinic_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[AiRegressionRun], int]:
        stmt = select(AiRegressionRun).where(AiRegressionRun.clinic_id == clinic_id)
        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                stmt.order_by(AiRegressionRun.created_at.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
        return list(rows), int(total)

    async def results(self, run_id: uuid.UUID) -> list[tuple[AiRegressionResult, AiRegressionCase]]:
        rows = (
            await self._session.execute(
                select(AiRegressionResult, AiRegressionCase)
                .join(AiRegressionCase, AiRegressionCase.id == AiRegressionResult.case_id)
                .where(AiRegressionResult.run_id == run_id)
                .order_by(AiRegressionResult.status, AiRegressionResult.created_at)
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    async def evaluated_case_ids(self, run_id: uuid.UUID) -> set[uuid.UUID]:
        """Чтобы повтор задания не оценивал уже оценённое."""
        rows = (
            await self._session.execute(
                select(AiRegressionResult.case_id).where(AiRegressionResult.run_id == run_id)
            )
        ).scalars()
        return set(rows)
