"""Сборка роутов отчётов: клиентский контур (/clinic/reports) и платформенный (/admin/reports).

Контуры разделены намеренно: клиентский защищён RBAC клиники
(``reports.view_all``/``reports.view_own``), платформенный — признаком
``is_platform_admin`` и работает по любой клинике без own-ограничений.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.reports.presentation.routes import (
    admin_clinic_doctors,
    admin_clinic_revenue,
    admin_clinic_services,
    admin_clinic_summary,
    admin_clinics,
    doctors,
    revenue,
    services,
    summary,
)

router = APIRouter(tags=["reports"])
router.include_router(summary.router)
router.include_router(revenue.router)
router.include_router(doctors.router)
router.include_router(services.router)

admin_router = APIRouter(tags=["reports-admin"])
admin_router.include_router(admin_clinics.router)
admin_router.include_router(admin_clinic_summary.router)
admin_router.include_router(admin_clinic_revenue.router)
admin_router.include_router(admin_clinic_doctors.router)
admin_router.include_router(admin_clinic_services.router)
