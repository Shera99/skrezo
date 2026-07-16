"""Сборка роутов услуг под /clinic/services."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.services.presentation.routes import create_service, list_services, update_service

router = APIRouter(tags=["services"])
router.include_router(list_services.router)
router.include_router(create_service.router)
router.include_router(update_service.router)
