"""Сборка роутов демо-клиники: сброс — клинике, признак — команде Skrezo."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.demo.presentation.routes import admin_set_demo, reset

router = APIRouter(tags=["clinic-settings"])
router.include_router(reset.router)

admin_router = APIRouter(tags=["clients"])
admin_router.include_router(admin_set_demo.router)
