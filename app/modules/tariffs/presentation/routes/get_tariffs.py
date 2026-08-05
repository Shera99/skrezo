"""Роут: GET /admin/tariffs — текущие тарифы (solo+clinic) и история версий."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.tariffs.application.use_cases.get_tariffs import GetTariffsUseCase, VersionView
from app.modules.tariffs.domain.pricing import PackageDef, package_summary
from app.modules.tariffs.presentation.deps import get_tariffs_use_case
from app.modules.tariffs.presentation.schemas import (
    ClinicTariffOut,
    PackageSchema,
    SoloTariffOut,
    TariffHistoryItem,
    TariffsBundle,
)
from app.shared.db.enums import ClinicType

router = APIRouter()


def _summary(view: VersionView) -> str:
    version = view.version
    if version.type is ClinicType.SOLO:
        return f"{version.price_tiyin}/мес"
    packages = [PackageDef(p.name, p.up_to, p.price_tiyin) for p in view.packages]
    return package_summary(packages, version.over_price_tiyin or 0)


@router.get(
    "/admin/tariffs",
    response_model=TariffsBundle,
    summary="Текущие тарифы и история",
    response_description="Активные версии solo/clinic и история всех версий",
)
async def get_tariffs(
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetTariffsUseCase = Depends(get_tariffs_use_case),
) -> TariffsBundle:
    result = await use_case.execute()

    solo = None
    if result.solo is not None and result.solo.price_tiyin is not None:
        solo = SoloTariffOut(
            version=result.solo.version,
            updated_at=result.solo.created_at,
            price_tiyin=result.solo.price_tiyin,
        )

    clinic = None
    if result.clinic is not None:
        clinic = ClinicTariffOut(
            version=result.clinic.version,
            updated_at=result.clinic.created_at,
            packages=[
                PackageSchema(name=p.name, up_to=p.up_to, price_tiyin=p.price_tiyin)
                for p in result.clinic_packages
            ],
            over_price_tiyin=result.clinic.over_price_tiyin or 0,
        )

    current_ids = {v.id for v in (result.solo, result.clinic) if v is not None}
    history = [
        TariffHistoryItem(
            id=view.version.id,
            kind=view.version.type.value,
            version=view.version.version,
            date=view.version.created_at,
            current=view.version.id in current_ids,
            summary=_summary(view),
        )
        for view in result.history
    ]
    return TariffsBundle(solo=solo, clinic=clinic, history=history)
