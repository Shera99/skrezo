"""Роут: GET /admin/payments — журнал платежей + сводка за период.

v1 — заглушка: журнал пуст, сводка нулевая (биллинг отложен). Параметры фильтра
принимаются для совместимости контракта.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.modules.payments.presentation.schemas import PaymentsBundle, PaymentSummary
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()


def _period_label(date_from: date | None, date_to: date | None) -> str:
    if date_from and date_to:
        return f"{date_from.isoformat()} — {date_to.isoformat()}"
    return "Весь период"


@router.get(
    "/admin/payments",
    response_model=PaymentsBundle,
    summary="Журнал платежей и сводка (v1 — заглушка)",
    response_description="Сводка за период и строки журнала (пусто в v1)",
)
async def list_payments(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    query: str | None = Query(default=None, description="Поиск по клиенту"),
    status: str | None = Query(default=None, description="success | failed | refund"),
    page: int = Query(default=1, ge=1),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
) -> PaymentsBundle:
    return PaymentsBundle(
        summary=PaymentSummary(period_label=_period_label(date_from, date_to)),
        rows=[],
        total_count=0,
    )
