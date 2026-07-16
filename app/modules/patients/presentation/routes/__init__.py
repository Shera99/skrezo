"""Сборка роутов пациентов под /clinic/patients."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.patients.presentation.routes import (
    create_patient,
    get_patient,
    list_patients,
    lookup_patient,
    tooth_history,
    update_patient,
    upsert_teeth,
)

router = APIRouter(tags=["patients"])
router.include_router(list_patients.router)
router.include_router(create_patient.router)
# Статичный /lookup — до параметрического /{patient_id}.
router.include_router(lookup_patient.router)
router.include_router(get_patient.router)
router.include_router(update_patient.router)
router.include_router(upsert_teeth.router)
router.include_router(tooth_history.router)
