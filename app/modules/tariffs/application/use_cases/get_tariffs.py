"""Use-case: бандл тарифов (текущие solo+clinic + история версий)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.modules.tariffs.infrastructure.repositories.tariff_repository import TariffRepository
from app.shared.db.enums import ClinicType
from app.shared.db.models.tariff_package import TariffPackage
from app.shared.db.models.tariff_version import TariffVersion


@dataclass(slots=True)
class VersionView:
    version: TariffVersion
    packages: Sequence[TariffPackage]  # пусто для solo


@dataclass(slots=True)
class GetTariffsResult:
    solo: TariffVersion | None
    clinic: TariffVersion | None
    clinic_packages: Sequence[TariffPackage]
    history: list[VersionView]  # обе линейки, по убыванию created_at


class GetTariffsUseCase:
    def __init__(self, tariffs: TariffRepository) -> None:
        self._tariffs = tariffs

    async def execute(self) -> GetTariffsResult:
        solo = await self._tariffs.get_current(ClinicType.SOLO)
        clinic = await self._tariffs.get_current(ClinicType.CLINIC)
        clinic_packages = await self._tariffs.list_packages(clinic.id) if clinic else []

        history: list[VersionView] = [
            VersionView(version, [])
            for version in await self._tariffs.list_versions(ClinicType.SOLO)
        ]
        for version in await self._tariffs.list_versions(ClinicType.CLINIC):
            packages = await self._tariffs.list_packages(version.id)
            history.append(VersionView(version, packages))
        history.sort(key=lambda v: v.version.created_at, reverse=True)
        return GetTariffsResult(solo, clinic, clinic_packages, history)
