"""Зависимости модуля тарифов."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.tariffs.application.use_cases.get_tariffs import GetTariffsUseCase
from app.modules.tariffs.application.use_cases.update_clinic_tariff import UpdateClinicTariffUseCase
from app.modules.tariffs.application.use_cases.update_solo_tariff import UpdateSoloTariffUseCase
from app.modules.tariffs.infrastructure.repositories.tariff_repository import TariffRepository


def get_tariff_repository(session: AsyncSession = Depends(get_session)) -> TariffRepository:
    return TariffRepository(session)


def get_tariffs_use_case(
    tariffs: TariffRepository = Depends(get_tariff_repository),
) -> GetTariffsUseCase:
    return GetTariffsUseCase(tariffs)


def get_update_solo_use_case(
    session: AsyncSession = Depends(get_session),
    tariffs: TariffRepository = Depends(get_tariff_repository),
) -> UpdateSoloTariffUseCase:
    return UpdateSoloTariffUseCase(session, tariffs)


def get_update_clinic_use_case(
    session: AsyncSession = Depends(get_session),
    tariffs: TariffRepository = Depends(get_tariff_repository),
) -> UpdateClinicTariffUseCase:
    return UpdateClinicTariffUseCase(session, tariffs)
