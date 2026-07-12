"""Сборка роутов модуля справочников в один APIRouter."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.catalog.presentation.routes import admin_refs, list_cities, list_specializations

router = APIRouter(tags=["catalog"])
router.include_router(list_cities.router)
router.include_router(list_specializations.router)
router.include_router(admin_refs.router)
