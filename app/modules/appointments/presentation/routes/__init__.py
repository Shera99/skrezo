"""Сборка роутов приёмов под /clinic/appointments."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.appointments.presentation.routes import (
    create_appointment,
    get_appointment,
    list_appointments,
    update_appointment,
)

router = APIRouter(tags=["appointments"])
router.include_router(list_appointments.router)
router.include_router(create_appointment.router)
router.include_router(get_appointment.router)
router.include_router(update_appointment.router)
