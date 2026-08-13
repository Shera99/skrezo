"""Репозиторий услуг клиники (прайс)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.membership import Membership
from app.shared.db.models.service import Service
from app.shared.db.models.service_doctor import ServiceDoctor
from app.shared.db.updates import apply_updates

# Колонки услуги, которые разрешено править через PATCH. Здесь нет clinic_id:
# иначе тело запроса уводит услугу в чужую клинику.
_UPDATABLE_FIELDS = frozenset(
    {"name", "description", "price_tiyin", "duration", "active", "tooth_outcome"}
)
# Из них — NOT NULL в БД.
_NOT_NULL_FIELDS = frozenset({"name", "price_tiyin", "active"})

# Псевдонимы на уровне модуля: внутри класса имя ``list`` занято методом
# ``ServiceRepository.list``, и аннотация ``list[...]`` там читается как он.
_DoctorIds = list[uuid.UUID]
_DoctorsByService = dict[uuid.UUID, _DoctorIds]


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

    # ── Врачи услуги ────────────────────────────────────────────────────────

    async def doctors_by_service(
        self, service_ids: Sequence[uuid.UUID]
    ) -> _DoctorsByService:
        """Назначенные врачи для набора услуг — одним запросом (иначе N+1).

        В словарь попадают только услуги, у которых назначения есть: их
        отсутствие и означает «делают все», и отличить это от «назначения есть,
        но все уволены» можно только по наличию ключа.
        """
        if not service_ids:
            return {}
        rows = (
            await self._session.execute(
                select(ServiceDoctor.service_id, ServiceDoctor.membership_id, Membership.active)
                .join(Membership, Membership.id == ServiceDoctor.membership_id)
                .where(ServiceDoctor.service_id.in_(service_ids))
            )
        ).all()
        result: _DoctorsByService = {}
        for service_id, membership_id, active in rows:
            # Ключ заводим на любое назначение, а в список кладём только живых:
            # уволенных из связи не вычищаем, чтобы не терять настройку прайса.
            doctors = result.setdefault(service_id, [])
            if active:
                doctors.append(membership_id)
        return result

    async def set_doctors(
        self, service_id: uuid.UUID, membership_ids: Sequence[uuid.UUID] | None
    ) -> None:
        """Заменить набор врачей услуги; ``None`` — снять ограничение."""
        await self._session.execute(
            delete(ServiceDoctor).where(ServiceDoctor.service_id == service_id)
        )
        if membership_ids:
            self._session.add_all(
                [
                    ServiceDoctor(service_id=service_id, membership_id=m)
                    for m in dict.fromkeys(membership_ids)  # дедуп, порядок сохраняем
                ]
            )
        await self._session.flush()

    async def unknown_doctors(
        self, clinic_id: uuid.UUID, membership_ids: Sequence[uuid.UUID]
    ) -> _DoctorIds:
        """Из переданных id — те, что не являются врачами этой клиники.

        «Врач» здесь тот же, что и везде: активное членство со специализацией.
        Иначе на услугу можно назначить менеджера, к которому потом всё равно
        нельзя записать (``invalid_doctor`` при создании приёма).
        """
        if not membership_ids:
            return []
        known = set(
            (
                await self._session.execute(
                    select(Membership.id).where(
                        Membership.id.in_(membership_ids),
                        Membership.clinic_id == clinic_id,
                        Membership.active.is_(True),
                        Membership.specialization_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [m for m in dict.fromkeys(membership_ids) if m not in known]
