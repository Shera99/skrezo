"""Роуты Skrezo: журнал клиента и ручные операции (перевод на счёт, корректировка)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFoundError
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.payments.application.services.ledger_service import LedgerService
from app.modules.payments.domain.ledger import KIND_CREDIT, SOURCE_ADJUSTMENT, SOURCE_TOPUP
from app.modules.payments.presentation.deps import get_ledger_service
from app.modules.payments.presentation.schemas import (
    AdminLedgerEntryRequest,
    LedgerEntryOut,
    LedgerResponse,
)
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.presentation.deps import get_manage_subscription_use_case
from app.shared.db.models.clinic import Clinic

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {404: {"description": "Клиент не найден"}}


async def _clinic(session: AsyncSession, clinic_id: uuid.UUID) -> Clinic:
    clinic = await session.get(Clinic, clinic_id)
    if clinic is None:
        raise NotFoundError("Клиент не найден", code="client_not_found")
    return clinic


@router.get(
    "/admin/clients/{clinic_id}/ledger",
    response_model=LedgerResponse,
    summary="Баланс и журнал операций клиента",
    responses=_ERRORS,
)
async def admin_ledger(
    clinic_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    session: AsyncSession = Depends(get_session),
    ledger: LedgerService = Depends(get_ledger_service),
) -> LedgerResponse:
    """Та же выписка, что видит клиника, плюс остаток и автопродление."""
    await _clinic(session, clinic_id)
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
    "/admin/clients/{clinic_id}/ledger/entries",
    status_code=status.HTTP_201_CREATED,
    response_model=LedgerEntryOut,
    summary="Ручная операция: перевод на счёт или корректировка",
    responses=_ERRORS,
)
async def admin_ledger_entry(
    clinic_id: uuid.UUID,
    body: AdminLedgerEntryRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    session: AsyncSession = Depends(get_session),
    ledger: LedgerService = Depends(get_ledger_service),
    manage: ManageSubscriptionUseCase = Depends(get_manage_subscription_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> LedgerEntryOut:
    """`credit` — клиника перевела деньги на счёт (пополнение баланса; если
    подписка ждёт денег — продлится сразу); `debit` — корректировка с
    комментарием, остаток может уйти в минус. Возвраты — через журнал платежей."""
    clinic = await _clinic(session, clinic_id)
    credit = body.kind == KIND_CREDIT
    entry = await ledger.post(
        clinic_id,
        kind=body.kind,
        source=SOURCE_TOPUP if credit else SOURCE_ADJUSTMENT,
        amount_tiyin=body.amount_tiyin,
        description=body.comment,
        created_by=actor,
    )
    await session.commit()
    if credit:
        await ledger.try_autorenew(clinic_id, manage=manage, reason="autopay_balance")
    await audit.record(
        actor_id=actor,
        action="client.ledger_entry",
        entity_type="clinic",
        entity_id=clinic.id,
        payload={"kind": body.kind, "amountTiyin": body.amount_tiyin, "comment": body.comment},
    )
    await session.refresh(entry)
    return LedgerEntryOut.from_orm_entry(entry)
