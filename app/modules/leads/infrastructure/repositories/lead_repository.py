"""Репозиторий лидов."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.lead import Lead


class LeadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_phone(self, phone: str) -> Lead | None:
        return (
            await self._session.execute(select(Lead).where(Lead.phone == phone))
        ).scalar_one_or_none()

    async def create(
        self,
        *,
        full_name: str,
        phone: str,
        clinic_name: str,
        doctors_count_range: str | None,
        comment: str | None,
    ) -> Lead:
        lead = Lead(
            full_name=full_name,
            phone=phone,
            clinic_name=clinic_name,
            doctors_count_range=doctors_count_range,
            comment=comment,
        )
        self._session.add(lead)
        await self._session.flush()
        return lead

    async def mark_converted(self, lead_id: uuid.UUID, clinic_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Lead).where(Lead.id == lead_id).values(converted_clinic_id=clinic_id)
        )
        await self._session.flush()
