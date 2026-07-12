"""Сборка роутов тарифов под /admin/tariffs."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.tariffs.presentation.routes import (
    get_tariffs,
    update_clinic_tariff,
    update_solo_tariff,
)

router = APIRouter(tags=["tariffs"])
router.include_router(get_tariffs.router)
router.include_router(update_solo_tariff.router)
router.include_router(update_clinic_tariff.router)
