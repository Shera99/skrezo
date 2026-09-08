"""Роут: GET /admin/payments — журнал платежей за подписку + сводка за период."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.modules.payments.presentation.deps import get_payment_repository
from app.modules.payments.presentation.schemas import PaymentRow, PaymentsBundle, PaymentSummary
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.platform_payment import PlatformPayment

router = APIRouter()

_STATUS = {"paid": "success", "failed": "failed", "refunded": "refund", "pending": "pending"}


def _period_label(date_from: date | None, date_to: date | None) -> str:
    if date_from and date_to:
        return f"{date_from.isoformat()} — {date_to.isoformat()}"
    return "Весь период"


def _row(payment: PlatformPayment, clinic: Clinic) -> PaymentRow:
    refund = payment.status == "refunded"
    period = (
        f"{payment.period_start:%d.%m.%Y} — {payment.period_end:%d.%m.%Y}"
        if payment.period_start and payment.period_end
        else "—"
    )
    return PaymentRow(
        id=str(payment.id),
        date=payment.paid_at or payment.created_at,
        client=clinic.name,
        client_type=clinic.type.value,
        type="Возврат" if refund else "Оплата подписки",
        period=period,
        amount_tiyin=-payment.amount_tiyin if refund else payment.amount_tiyin,
        status=_STATUS.get(payment.status, payment.status),
        method="FreedomPay",
        txn_id=payment.provider_payment_id or "",
    )


@router.get(
    "/admin/payments",
    response_model=PaymentsBundle,
    summary="Журнал платежей за подписку и сводка",
    response_description="Сводка за период и строки журнала (новые сверху)",
)
async def list_payments(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    query: str | None = Query(default=None, description="Поиск по клиенту или id у провайдера"),
    status: str | None = Query(default=None, description="success | failed | refund | pending"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    repository: PlatformPaymentRepository = Depends(get_payment_repository),
) -> PaymentsBundle:
    """Платежи клиник через FreedomPay: успешные открывают период, возвраты
    отмечает команда Skrezo вручную (`/admin/payments/{id}/refund`)."""
    rows, total = await repository.list_for_platform(
        date_from=date_from,
        date_to=date_to,
        query=(query or "").strip() or None,
        status=status,
        page=page,
        page_size=page_size,
    )
    summary = await repository.summary(date_from, date_to)
    return PaymentsBundle(
        summary=PaymentSummary(
            period_label=_period_label(date_from, date_to),
            net_tiyin=summary["success_sum_tiyin"] + summary["refund_sum_tiyin"],
            **summary,
        ),
        rows=[_row(payment, clinic) for payment, clinic in rows],
        total_count=total,
    )
