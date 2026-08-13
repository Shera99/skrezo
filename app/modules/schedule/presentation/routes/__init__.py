"""Сборка роутов расписания под /clinic/schedule."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.schedule.presentation.routes import (
    clear_doctor_hours,
    create_time_off,
    delete_time_off,
    get_day,
    get_doctor_hours,
    get_slots,
    get_working_hours,
    list_time_off,
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
router.include_router(clear_doctor_hours.router)
router.include_router(list_time_off.router)
router.include_router(create_time_off.router)
router.include_router(delete_time_off.router)
