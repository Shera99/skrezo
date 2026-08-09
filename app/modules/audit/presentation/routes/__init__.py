"""Сборка роутов аудита: платформенный журнал и журнал клиники."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.audit.presentation.routes import clinic_audit, list_audit

router = APIRouter(tags=["audit"])
router.include_router(list_audit.router)

# Журнал клиники — отдельный роутер и тег: контур другой (право клиники вместо
# платформенного гварда), и в OpenAPI они не должны выглядеть одним разделом.
clinic_router = APIRouter(tags=["clinic-audit"])
clinic_router.include_router(clinic_audit.router)
