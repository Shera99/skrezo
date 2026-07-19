"""Сборка роутов кассы под /clinic/cash."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.cash.presentation.routes import (
    cash_summary,
    list_debts,
    list_payments,
    record_payment,
    refund_payment,
)

router = APIRouter(tags=["cash"])
router.include_router(list_payments.router)
router.include_router(record_payment.router)
router.include_router(refund_payment.router)
router.include_router(cash_summary.router)
router.include_router(list_debts.router)
