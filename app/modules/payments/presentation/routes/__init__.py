"""Сборка роутов платежей: журнал Skrezo, оплата подписки клиникой, колбэк провайдера."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.payments.presentation.routes import (
    clinic_payments,
    freedompay_result,
    list_payments,
    refund_payment,
)

router = APIRouter(tags=["payments"])
router.include_router(list_payments.router)
router.include_router(refund_payment.router)
router.include_router(clinic_payments.router)
router.include_router(freedompay_result.router)
