"""Use-case: обновить тариф соло (создаёт новую версию)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tariffs.infrastructure.repositories.tariff_repository import TariffRepository
from app.shared.db.enums import ClinicType


@dataclass(slots=True)
class UpdateSoloResult:
    version_id: uuid.UUID
    version: int


class UpdateSoloTariffUseCase:
    def __init__(self, session: AsyncSession, tariffs: TariffRepository) -> None:
        self._session = session
        self._tariffs = tariffs

    async def execute(self, price: int, created_by: uuid.UUID) -> UpdateSoloResult:
        version_no = await self._tariffs.next_version(ClinicType.SOLO)
        row = await self._tariffs.create_version(
            tariff_type=ClinicType.SOLO,
            version=version_no,
            price=price,
            over_price=None,
            created_by=created_by,
        )
        await self._session.commit()
        return UpdateSoloResult(version_id=row.id, version=version_no)
