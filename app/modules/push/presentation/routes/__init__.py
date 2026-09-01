"""Сборка роутов уведомлений: устройства, префы, инбокс."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.push.presentation.routes import (
    inbox,
    register_device,
    unregister_device,
    update_prefs,
)

router = APIRouter(tags=["push"])
router.include_router(register_device.router)
router.include_router(unregister_device.router)
router.include_router(update_prefs.router)
router.include_router(inbox.router)
