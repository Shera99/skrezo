"""Сборка роутов модуля пациентов под /clinic/patients."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.patients.presentation.routes import (
    create_patient,
    create_visit,
    get_patient,
    get_visit,
    list_patients,
    list_visits,
    tooth_history,
    update_patient,
)

router = APIRouter(tags=["patients"])
router.include_router(list_patients.router)
router.include_router(create_patient.router)
router.include_router(get_patient.router)
router.include_router(update_patient.router)
router.include_router(tooth_history.router)
router.include_router(list_visits.router)
router.include_router(create_visit.router)
router.include_router(get_visit.router)
