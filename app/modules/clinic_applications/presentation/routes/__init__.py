"""Сборка роутов заявок клиник: публичная подача и админ-рассмотрение.

Контуры разделены: подача (``POST /clinic-applications``) публичная и защищена
билетом OTP; просмотр и смена статуса — только для команды Skrezo.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.clinic_applications.presentation.routes import (
    get_application,
    list_applications,
    set_application_status,
    submit_application,
)

router = APIRouter(tags=["clinic-applications"])
router.include_router(submit_application.router)

admin_router = APIRouter(tags=["clinic-applications-admin"])
admin_router.include_router(list_applications.router)
admin_router.include_router(get_application.router)
admin_router.include_router(set_application_status.router)
