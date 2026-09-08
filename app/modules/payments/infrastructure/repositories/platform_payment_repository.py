"""Репозиторий платежей за подписку: свои для клиники, все — для Skrezo."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.clinic import Clinic
from app.shared.db.models.platform_payment import PlatformPayment


class PlatformPaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, payment_id: uuid.UUID) -> PlatformPayment | None:
        return await self._session.get(PlatformPayment, payment_id)

    async def get_for_clinic(
        self, clinic_id: uuid.UUID, payment_id: uuid.UUID
    ) -> PlatformPayment | None:
        payment = await self.get(payment_id)
        return payment if payment is not None and payment.clinic_id == clinic_id else None

    async def list_for_clinic(
        self, clinic_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[PlatformPayment], int]:
        stmt = select(PlatformPayment).where(PlatformPayment.clinic_id == clinic_id)
        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                stmt.order_by(PlatformPayment.created_at.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
        return list(rows), int(total)

    async def list_for_platform(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        query: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[PlatformPayment, Clinic]], int]:
        stmt = select(PlatformPayment, Clinic).join(Clinic, Clinic.id == PlatformPayment.clinic_id)
        if date_from is not None:
            stmt = stmt.where(
                PlatformPayment.created_at
                >= datetime.combine(date_from, datetime.min.time()).astimezone()
            )
        if date_to is not None:
            stmt = stmt.where(
                PlatformPayment.created_at
                < datetime.combine(date_to + timedelta(days=1), datetime.min.time()).astimezone()
            )
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                or_(Clinic.name.ilike(pattern), PlatformPayment.provider_payment_id.ilike(pattern))
            )
        if status:
            stmt = stmt.where(PlatformPayment.status.in_(_STATUS_FILTER.get(status, [status])))
        total = (
            await self._session.execute(
                select(func.count()).select_from(stmt.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                stmt.order_by(PlatformPayment.created_at.desc())
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).all()
        return [(row[0], row[1]) for row in rows], int(total)

    async def summary(self, date_from: date | None, date_to: date | None) -> dict[str, Any]:
        conditions = []
        if date_from is not None:
            conditions.append(
                PlatformPayment.created_at
                >= datetime.combine(date_from, datetime.min.time()).astimezone()
            )
        if date_to is not None:
            conditions.append(
                PlatformPayment.created_at
                < datetime.combine(date_to + timedelta(days=1), datetime.min.time()).astimezone()
            )
        row = (
            await self._session.execute(
                select(
                    func.coalesce(
                        func.sum(PlatformPayment.amount_tiyin).filter(
                            PlatformPayment.status.in_(_SETTLED)
                        ),
                        0,
                    ),
                    func.count().filter(PlatformPayment.status.in_(_SETTLED)),
                    func.count().filter(PlatformPayment.status == "failed"),
                    func.coalesce(
                        func.sum(PlatformPayment.amount_tiyin).filter(
                            PlatformPayment.status == "failed"
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(PlatformPayment.amount_tiyin).filter(
                            PlatformPayment.status == "refunded"
                        ),
                        0,
                    ),
                    func.count().filter(PlatformPayment.status == "refunded"),
                ).where(*conditions)
            )
        ).one()
        return {
            "success_sum_tiyin": int(row[0]),
            "success_count": int(row[1]),
            "failed_count": int(row[2]),
            "failed_sum_tiyin": int(row[3]),
            "refund_sum_tiyin": -int(row[4]),
            "refund_count": int(row[5]),
        }


#: Успешно полученные деньги — и те, что потом вернули: возврат считается отдельно,
#: иначе после возврата пропадал бы и сам платёж, и сходимость «успешно − возвраты».
_SETTLED = ("paid", "refunded")

#: Фильтр статуса из контракта фронта → статусы строк.
_STATUS_FILTER: dict[str, list[str]] = {
    "success": ["paid"],
    "failed": ["failed"],
    "refund": ["refunded"],
    "pending": ["pending"],
}
