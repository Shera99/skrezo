"""Роуты кабинета: баланс и журнал операций, оплата подписки с баланса, автопродление."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.payments.application.services.ledger_service import LedgerService
from app.modules.payments.presentation.deps import get_ledger_service
from app.modules.payments.presentation.schemas import (
    AutopayRequest,
    LedgerEntryOut,
    LedgerResponse,
    PayFromBalanceRequest,
)
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.presentation.deps import get_manage_subscription_use_case
from app.shared.auth import ClientContext

router = APIRouter()

_PAY_ERRORS: dict[int | str, dict[str, Any]] = {
    402: {
        "description": "На балансе не хватает — в ответе недостача",
        "content": {
            "application/json": {
                "example": {
                    "error": "insufficient_balance",
                    "message": "На балансе не хватает на продление — пополните баланс",
                    "balanceTiyin": 100000,
                    "amountTiyin": 300000,
                    "shortfallTiyin": 200000,
                }
            }
        },
    },
    409: {
        "description": (
            "Тариф не настроен или подписку открывает только Skrezo: ручной блок "
            "(`subscription_blocked`) и уход клиента (`subscription_cancelled`) деньгами "
            "с баланса не снимаются"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "subscription_blocked",
                    "message": "Подписка заблокирована Skrezo — оплата её не откроет, напишите нам",
                }
            }
        },
    },
}


def _clinic(context: ClientContext) -> uuid.UUID:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    return context.clinic_id


@router.get(
    "/clinic/ledger",
    response_model=LedgerResponse,
    summary="Баланс и журнал операций клиники",
    response_description="Остаток, автопродление и выписка (новые сверху)",
)
async def clinic_ledger(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    context: ClientContext = Depends(require_permission("billing.view")),
    ledger: LedgerService = Depends(get_ledger_service),
) -> LedgerResponse:
    """Каждая строка — приход или расход с остатком после операции: оплаты через
    провайдера, переводы на счёт, списания за подписку, возвраты."""
    clinic_id = _clinic(context)
    wallet = await ledger.wallet(clinic_id)
    rows, total = await ledger.entries(clinic_id, page=page, page_size=page_size)
    return LedgerResponse(
        balance_tiyin=wallet.balance_tiyin,
        autopay_enabled=wallet.autopay_enabled,
        list=[LedgerEntryOut.from_orm_entry(e) for e in rows],
        current_page=page,
        page_size=page_size,
        total_count=total,
    )


@router.post(
    "/clinic/subscription/pay-from-balance",
    status_code=status.HTTP_201_CREATED,
    response_model=LedgerEntryOut,
    summary="Оплатить подписку с баланса",
    response_description="Строка списания с оплаченным периодом",
    responses=_PAY_ERRORS,
)
async def pay_from_balance(
    body: PayFromBalanceRequest,
    context: ClientContext = Depends(require_permission("billing.manage")),
    ledger: LedgerService = Depends(get_ledger_service),
    manage: ManageSubscriptionUseCase = Depends(get_manage_subscription_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> LedgerEntryOut:
    """Цена по тарифу за N месяцев списывается с баланса, период открывается
    от конца текущего. Не хватает — 402 с недостачей, журнал не меняется."""
    clinic_id = _clinic(context)
    entry, quote = await ledger.charge_subscription(
        clinic_id,
        body.months,
        manage=manage,
        created_by=context.account_id,
        reason="balance",
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="subscription.paid_from_balance",
        entity_type="clinic_ledger_entry",
        entity_id=entry.id,
        payload={"months": body.months, "amountTiyin": quote.total_tiyin},
    )
    return LedgerEntryOut.from_orm_entry(entry)


@router.patch(
    "/clinic/subscription/autopay",
    response_model=LedgerResponse,
    summary="Включить или выключить автопродление с баланса",
)
async def set_autopay(
    body: AutopayRequest,
    context: ClientContext = Depends(require_permission("billing.manage")),
    ledger: LedgerService = Depends(get_ledger_service),
    audit: AuditService = Depends(get_audit_service),
) -> LedgerResponse:
    """Включено — за `AUTOPAY_DAYS_BEFORE` дней до конца периода месяц списывается
    с баланса сам; не хватает — владельцу приходит сообщение с суммой. Позже,
    когда провайдер откроет автосписание, недостающее спишется с карты."""
    clinic_id = _clinic(context)
    wallet = await ledger.set_autopay(clinic_id, body.enabled)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="subscription.autopay",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"enabled": body.enabled},
    )
    rows, total = await ledger.entries(clinic_id, page=1, page_size=25)
    return LedgerResponse(
        balance_tiyin=wallet.balance_tiyin,
        autopay_enabled=wallet.autopay_enabled,
        list=[LedgerEntryOut.from_orm_entry(e) for e in rows],
        current_page=1,
        page_size=25,
        total_count=total,
    )
