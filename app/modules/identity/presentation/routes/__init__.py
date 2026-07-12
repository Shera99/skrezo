"""Сборка роутов клиентской аутентификации под /auth."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.identity.presentation.routes import (
    change_password,
    login,
    logout,
    me,
    refresh,
    select_clinic,
)

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(login.router)
router.include_router(change_password.router)
router.include_router(select_clinic.router)
router.include_router(refresh.router)
router.include_router(logout.router)
router.include_router(me.router)
