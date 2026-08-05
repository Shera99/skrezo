"""Use-case: обновить тариф клиники (валидирует пакеты, создаёт новую версию)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tariffs.domain.pricing import PackageDef, validate_packages
from app.modules.tariffs.infrastructure.repositories.tariff_repository import TariffRepository
from app.shared.db.enums import ClinicType


@dataclass(slots=True)
class UpdateClinicResult:
    version_id: uuid.UUID
    version: int
    affected_clients: int


class UpdateClinicTariffUseCase:
    def __init__(self, session: AsyncSession, tariffs: TariffRepository) -> None:
        self._session = session
        self._tariffs = tariffs

    async def execute(
        self, packages: list[PackageDef], over_price: int, created_by: uuid.UUID
    ) -> UpdateClinicResult:
        validate_packages(packages, over_price)  # бросает 422 при ошибке

        version_no = await self._tariffs.next_version(ClinicType.CLINIC)
        row = await self._tariffs.create_version(
            tariff_type=ClinicType.CLINIC,
            version=version_no,
            price=None,
            over_price=over_price,
            created_by=created_by,
        )
        for pkg in packages:
            await self._tariffs.add_package(
                version_id=row.id, name=pkg.name, up_to=pkg.up_to, price=pkg.price
            )
        affected = await self._tariffs.count_active_clinics()
        await self._session.commit()
        return UpdateClinicResult(version_id=row.id, version=version_no, affected_clients=affected)
