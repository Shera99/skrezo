"""Сборка роутов клиентской аутентификации под /auth."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.identity.presentation.routes import (
    change_password,
    login,
    logout,
    me,
    otp_request,
    otp_verify,
    password_forgot,
    password_reset,
    refresh,
    select_clinic,
    solo_register,
    update_profile,
)

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(login.router)
router.include_router(otp_request.router)
router.include_router(otp_verify.router)
router.include_router(solo_register.router)
router.include_router(change_password.router)
router.include_router(password_forgot.router)
router.include_router(password_reset.router)
router.include_router(select_clinic.router)
router.include_router(refresh.router)
router.include_router(logout.router)
router.include_router(me.router)
router.include_router(update_profile.router)
