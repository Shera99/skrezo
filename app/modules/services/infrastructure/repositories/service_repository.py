"""Репозиторий услуг клиники (прайс)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.service import Service
from app.shared.db.updates import apply_updates

# Колонки услуги, которые разрешено править через PATCH. Здесь нет clinic_id:
# иначе тело запроса уводит услугу в чужую клинику.
_UPDATABLE_FIELDS = frozenset(
    {"name", "description", "price_tiyin", "duration", "active", "tooth_outcome"}
)
# Из них — NOT NULL в БД.
_NOT_NULL_FIELDS = frozenset({"name", "price_tiyin", "active"})


class ServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, clinic_id: uuid.UUID, *, active_only: bool) -> Sequence[Service]:
        stmt = select(Service).where(Service.clinic_id == clinic_id)
        if active_only:
            stmt = stmt.where(Service.active.is_(True))
        return (await self._session.execute(stmt.order_by(Service.name))).scalars().all()

    async def get(self, clinic_id: uuid.UUID, service_id: uuid.UUID) -> Service | None:
        service = await self._session.get(Service, service_id)
        return service if service is not None and service.clinic_id == clinic_id else None

    async def create(self, clinic_id: uuid.UUID, **kwargs: Any) -> Service:
        service = Service(clinic_id=clinic_id, **kwargs)
        self._session.add(service)
        await self._session.flush()
        return service

    async def update(self, service: Service, fields: dict[str, Any]) -> None:
        """Записать разрешённые поля услуги."""
        apply_updates(
            service,
            fields,
            updatable=_UPDATABLE_FIELDS,
            not_null=_NOT_NULL_FIELDS,
            entity_name="услуги",
        )
        await self._session.flush()
