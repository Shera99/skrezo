"""Репозиторий пробелов знаний: склейка похожих, очередь, счётчики вкладок."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.domain.knowledge import SIMILARITY_THRESHOLD
from app.shared.db.models.ai_knowledge_gap import AiKnowledgeGap


class KnowledgeGapRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_open_similar(
        self, clinic_id: uuid.UUID, normalized: str
    ) -> AiKnowledgeGap | None:
        """Открытый пробел с той же или близкой формулировкой.

        Сначала точное совпадение нормализованной формы, потом триграммная
        близость (``pg_trgm``): опечатки и перестановки слов склеиваются, а вот
        «где припарковаться» и «есть ли парковка» — ещё нет, это про смысл, не
        про буквы (эмбеддинги — когда упрёмся).
        """
        base = select(AiKnowledgeGap).where(
            AiKnowledgeGap.clinic_id == clinic_id, AiKnowledgeGap.status == "unknown"
        )
        exact = (
            await self._session.execute(base.where(AiKnowledgeGap.normalized == normalized))
        ).scalar_one_or_none()
        if exact is not None:
            return exact
        similarity = func.similarity(AiKnowledgeGap.normalized, normalized)
        return (
            await self._session.execute(
                base.where(similarity > SIMILARITY_THRESHOLD).order_by(similarity.desc()).limit(1)
            )
        ).scalar_one_or_none()

    async def get(self, clinic_id: uuid.UUID, gap_id: uuid.UUID) -> AiKnowledgeGap | None:
        return (
            await self._session.execute(
                select(AiKnowledgeGap).where(
                    AiKnowledgeGap.id == gap_id, AiKnowledgeGap.clinic_id == clinic_id
                )
            )
        ).scalar_one_or_none()

    async def list_page(
        self,
        clinic_id: uuid.UUID,
        *,
        status: str | None,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AiKnowledgeGap], int]:
        stmt = select(AiKnowledgeGap).where(AiKnowledgeGap.clinic_id == clinic_id)
        if status:
            stmt = stmt.where(AiKnowledgeGap.status == status)
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                or_(AiKnowledgeGap.question.ilike(pattern), AiKnowledgeGap.hint.ilike(pattern))
            )
        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                # Самые частые сверху: их выгоднее всего закрыть первыми.
                stmt.order_by(AiKnowledgeGap.ask_count.desc(), AiKnowledgeGap.last_asked_at.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
        return list(rows), int(total)

    async def counts(self, clinic_id: uuid.UUID) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(AiKnowledgeGap.status, func.count())
                .where(AiKnowledgeGap.clinic_id == clinic_id)
                .group_by(AiKnowledgeGap.status)
            )
        ).all()
        counts = {"unknown": 0, "answered": 0, "dismissed": 0}
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts
