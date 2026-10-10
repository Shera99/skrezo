"""Сборка роутов заявок клиник: две публичные двери и админ-рассмотрение.

Очередь заявок одна. Подача через OTP (``POST /clinic-applications``) защищена
билетом OTP; заявка с лендинга (``POST /leads``) кода не требует и ограничена
rate-limit по IP; просмотр и смена статуса — только для команды Skrezo.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.clinic_applications.presentation.routes import (
    get_application,
    list_applications,
    set_application_status,
    submit_application,
    submit_lead,
)

router = APIRouter(tags=["clinic-applications"])
router.include_router(submit_application.router)

# Тег «leads» оставлен ради совместимости документации: ручка прежняя, но лид
# теперь попадает в общую очередь заявок.
leads_router = APIRouter(tags=["leads"])
leads_router.include_router(submit_lead.router)

admin_router = APIRouter(tags=["clinic-applications-admin"])
admin_router.include_router(list_applications.router)
admin_router.include_router(get_application.router)
admin_router.include_router(set_application_status.router)
