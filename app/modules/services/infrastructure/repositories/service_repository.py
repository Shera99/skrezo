"""Репозиторий услуг клиники (прайс)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.service import Service


class ServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, clinic_id: uuid.UUID, *, active_only: bool) -> Sequence[Service]:
        stmt = select(Service).where(Service.clinic_id == clinic_id)
        if active_only:
            stmt = stmt.where(Service.active.is_(True))
        return (
            (await self._session.execute(stmt.order_by(Service.name))).scalars().all()
        )

    async def get(self, clinic_id: uuid.UUID, service_id: uuid.UUID) -> Service | None:
        service = await self._session.get(Service, service_id)
        return service if service is not None and service.clinic_id == clinic_id else None

    async def create(self, clinic_id: uuid.UUID, **kwargs: Any) -> Service:
        service = Service(clinic_id=clinic_id, **kwargs)
        self._session.add(service)
        await self._session.flush()
        return service

    async def update(self, service: Service, fields: dict[str, Any]) -> None:
        for key, value in fields.items():
            setattr(service, key, value)
        await self._session.flush()
