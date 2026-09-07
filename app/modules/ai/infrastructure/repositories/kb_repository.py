"""Репозиторий базы знаний: документы, версии, сводка по языкам."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.kb_document import KbDocument
from app.shared.db.models.kb_document_version import KbDocumentVersion


class KbRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, clinic_id: uuid.UUID, document_id: uuid.UUID) -> KbDocument | None:
        return (
            await self._session.execute(
                select(KbDocument).where(
                    KbDocument.id == document_id, KbDocument.clinic_id == clinic_id
                )
            )
        ).scalar_one_or_none()

    async def list_page(
        self,
        clinic_id: uuid.UUID,
        *,
        lang: str | None,
        category: str | None,
        active: bool | None,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[KbDocument], int]:
        stmt = select(KbDocument).where(KbDocument.clinic_id == clinic_id)
        if lang:
            stmt = stmt.where(KbDocument.lang == lang)
        if category:
            stmt = stmt.where(KbDocument.category == category)
        if active is not None:
            stmt = stmt.where(KbDocument.is_active.is_(active))
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(or_(KbDocument.title.ilike(pattern), KbDocument.body.ilike(pattern)))
        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                stmt.order_by(KbDocument.updated_at.desc(), KbDocument.id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
        return list(rows), int(total)

    async def active_lengths(self, clinic_id: uuid.UUID) -> list[tuple[uuid.UUID, str, int]]:
        """(id, lang, длина заголовка+текста) активных документов в порядке сборки промпта."""
        rows = (
            await self._session.execute(
                select(
                    KbDocument.id,
                    KbDocument.lang,
                    func.length(KbDocument.title) + func.length(KbDocument.body),
                )
                .where(KbDocument.clinic_id == clinic_id, KbDocument.is_active.is_(True))
                .order_by(KbDocument.created_at, KbDocument.id)
            )
        ).all()
        return [(row[0], str(row[1]), int(row[2])) for row in rows]

    async def versions(self, document_id: uuid.UUID) -> list[KbDocumentVersion]:
        rows = (
            await self._session.execute(
                select(KbDocumentVersion)
                .where(KbDocumentVersion.document_id == document_id)
                .order_by(KbDocumentVersion.version_no.desc())
            )
        ).scalars()
        return list(rows)

    async def version(self, document_id: uuid.UUID, version_no: int) -> KbDocumentVersion | None:
        return (
            await self._session.execute(
                select(KbDocumentVersion).where(
                    KbDocumentVersion.document_id == document_id,
                    KbDocumentVersion.version_no == version_no,
                )
            )
        ).scalar_one_or_none()
