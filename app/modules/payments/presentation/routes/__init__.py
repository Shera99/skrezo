"""Сборка роутов платежей: журнал Skrezo, оплата подписки клиникой, колбэк провайдера.

Теги — по тому, кто читает ReDoc: журнал и возвраты — админка (``payments``),
цена и платежи своей клиники — раздел «Подписка клиники» (``subscription``),
серверный колбэк провайдера — публичное (``payments-webhook``).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.payments.presentation.routes import (
    admin_ledger,
    admin_plans,
    clinic_ledger,
    clinic_packages,
    clinic_payments,
    clinic_plan,
    freedompay_result,
    list_payments,
    refund_payment,
)

router = APIRouter()
router.include_router(list_payments.router, tags=["payments"])
router.include_router(refund_payment.router, tags=["payments"])
router.include_router(clinic_payments.router, tags=["subscription"])
router.include_router(clinic_packages.router, tags=["subscription"])
router.include_router(clinic_plan.router, tags=["subscription"])
router.include_router(clinic_ledger.router, tags=["subscription"])
router.include_router(admin_ledger.router, tags=["subscription-admin"])
router.include_router(admin_plans.router, tags=["subscription-admin"])
router.include_router(freedompay_result.router, tags=["payments-webhook"])
