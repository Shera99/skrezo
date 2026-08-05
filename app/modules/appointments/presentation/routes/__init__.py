"""Сборка роутов приёмов под /clinic/appointments."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.appointments.presentation.routes import (
    apply_discount,
    complete_appointment,
    create_appointment,
    get_appointment,
    list_appointments,
    replace_items,
    update_appointment,
)

router = APIRouter(tags=["appointments"])
router.include_router(list_appointments.router)
router.include_router(create_appointment.router)
router.include_router(get_appointment.router)
router.include_router(update_appointment.router)
router.include_router(replace_items.router)
router.include_router(apply_discount.router)
router.include_router(complete_appointment.router)
