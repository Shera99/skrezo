"""Сборка роутов аудита под /admin/audit."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.audit.presentation.routes import list_audit

router = APIRouter(tags=["audit"])
router.include_router(list_audit.router)
