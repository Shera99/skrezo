"""Сборка роутов управления сотрудниками под /clinic/staff."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.staff.presentation.routes import add_staff, list_staff, remove_staff, update_staff

router = APIRouter(tags=["staff"])
router.include_router(list_staff.router)
router.include_router(add_staff.router)
router.include_router(update_staff.router)
router.include_router(remove_staff.router)
