"""Сборка роутов расписания под /clinic/schedule."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.schedule.presentation.routes import (
    get_day,
    get_doctor_hours,
    get_slots,
    get_working_hours,
    set_doctor_hours,
    set_working_hours,
)

router = APIRouter(tags=["schedule"])
router.include_router(get_day.router)
router.include_router(get_slots.router)
router.include_router(get_working_hours.router)
router.include_router(set_working_hours.router)
router.include_router(get_doctor_hours.router)
router.include_router(set_doctor_hours.router)
