"""Репозиторий тарифов (версии + пакеты клиники)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import ClinicType, SubscriptionStatus
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.tariff_package import TariffPackage
from app.shared.db.models.tariff_version import TariffVersion


class TariffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(self, tariff_type: ClinicType) -> TariffVersion | None:
        """Текущая версия типа = запись с максимальным номером version."""
        return (
            await self._session.execute(
                select(TariffVersion)
                .where(TariffVersion.type == tariff_type)
                .order_by(TariffVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def list_packages(self, version_id: uuid.UUID) -> Sequence[TariffPackage]:
        return (
            (
                await self._session.execute(
                    select(TariffPackage)
                    .where(TariffPackage.tariff_version_id == version_id)
                    .order_by(TariffPackage.up_to)
                )
            )
            .scalars()
            .all()
        )

    async def list_versions(self, tariff_type: ClinicType) -> Sequence[TariffVersion]:
        return (
            (
                await self._session.execute(
                    select(TariffVersion)
                    .where(TariffVersion.type == tariff_type)
                    .order_by(TariffVersion.version.desc())
                )
            )
            .scalars()
            .all()
        )

    async def next_version(self, tariff_type: ClinicType) -> int:
        current = (
            await self._session.execute(
                select(func.max(TariffVersion.version)).where(TariffVersion.type == tariff_type)
            )
        ).scalar()
        return (current or 0) + 1

    async def create_version(
        self,
        *,
        tariff_type: ClinicType,
        version: int,
        price: int | None,
        over_price: int | None,
        created_by: uuid.UUID,
    ) -> TariffVersion:
        row = TariffVersion(
            type=tariff_type,
            version=version,
            price_tiyin=price,
            over_price_tiyin=over_price,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def add_package(
        self, *, version_id: uuid.UUID, name: str, up_to: int, price: int
    ) -> None:
        self._session.add(
            TariffPackage(tariff_version_id=version_id, name=name, up_to=up_to, price_tiyin=price)
        )
        await self._session.flush()

    async def count_active_clinics(self) -> int:
        """Число клиник (type=clinic) с не-отменённой подпиской — «затронет N»."""
        return (
            await self._session.execute(
                select(func.count())
                .select_from(Clinic)
                .outerjoin(Subscription, Subscription.clinic_id == Clinic.id)
                .where(
                    Clinic.type == ClinicType.CLINIC,
                    (Subscription.status.is_(None))
                    | (Subscription.status != SubscriptionStatus.CANCELLED),
                )
            )
        ).scalar_one()
