"""Репозиторий инбокса: вставка снимков адресатов, лента, прочитанность."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.inbox_item import InboxItem


class InboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_items(self, rows: list[dict[str, Any]]) -> list[int]:
        """Вставить строки адресатов; вернуть id ВСТАВЛЕННЫХ (дубли — молча мимо).

        Уникальный индекс + ``ON CONFLICT DO NOTHING`` и есть идемпотентность
        диспетчера: повторная обработка события никому ничего не дублирует —
        и не ставит повторных push_job'ов (их создают только новые id).
        """
        if not rows:
            return []
        result = await self._session.execute(
            pg_insert(InboxItem)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_inbox_item_event_account")
            .returning(InboxItem.id)
        )
        return list(result.scalars())

    async def list_page(
        self,
        account_id: uuid.UUID,
        clinic_id: uuid.UUID,
        *,
        unread_only: bool,
        before_id: int | None,
        limit: int,
    ) -> Sequence[InboxItem]:
        """Своя лента, новые сверху; курсор «старше id» — как история чата."""
        stmt = select(InboxItem).where(
            InboxItem.account_id == account_id, InboxItem.clinic_id == clinic_id
        )
        if unread_only:
            stmt = stmt.where(InboxItem.read_at.is_(None))
        if before_id is not None:
            stmt = stmt.where(InboxItem.id < before_id)
        rows = (
            await self._session.execute(stmt.order_by(InboxItem.id.desc()).limit(limit))
        ).scalars()
        return list(rows)

    async def unread_count(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(InboxItem)
                .where(
                    InboxItem.account_id == account_id,
                    InboxItem.clinic_id == clinic_id,
                    InboxItem.read_at.is_(None),
                )
            )
        ).scalar_one()

    async def mark_read(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID, item_id: int
    ) -> InboxItem | None:
        """Отметить прочитанным СВОЮ запись; чужая — как несуществующая (None)."""
        item = (
            await self._session.execute(
                select(InboxItem).where(
                    InboxItem.id == item_id,
                    InboxItem.account_id == account_id,
                    InboxItem.clinic_id == clinic_id,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            return None
        if item.read_at is None:
            item.read_at = datetime.now(UTC)
        return item

    async def mark_all_read(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> int:
        result = await self._session.execute(
            update(InboxItem)
            .where(
                InboxItem.account_id == account_id,
                InboxItem.clinic_id == clinic_id,
                InboxItem.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
            .returning(InboxItem.id)
        )
        return len(list(result.scalars()))
