"""Сборка роутов платежей под /admin/payments."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.payments.presentation.routes import list_payments, refund_payment

router = APIRouter(tags=["payments"])
router.include_router(list_payments.router)
router.include_router(refund_payment.router)
