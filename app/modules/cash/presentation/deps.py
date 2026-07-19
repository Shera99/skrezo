"""Зависимости модуля кассы."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.cash.application.services.cash_service import CashService, build_cash_service
from app.modules.cash.application.use_cases.cash_summary import CashSummaryUseCase
from app.modules.cash.application.use_cases.list_debts import ListDebtsUseCase
from app.modules.cash.application.use_cases.list_payments import ListPaymentsUseCase
from app.modules.cash.application.use_cases.record_payment import RecordPaymentUseCase
from app.modules.cash.application.use_cases.refund_payment import RefundPaymentUseCase
from app.modules.cash.infrastructure.repositories.cash_repository import CashRepository


def get_cash_repository(session: AsyncSession = Depends(get_session)) -> CashRepository:
    return CashRepository(session)


def get_cash_service(session: AsyncSession = Depends(get_session)) -> CashService:
    return build_cash_service(session)


def get_record_payment_use_case(
    session: AsyncSession = Depends(get_session),
    cash: CashService = Depends(get_cash_service),
) -> RecordPaymentUseCase:
    return RecordPaymentUseCase(session, cash)


def get_refund_payment_use_case(
    session: AsyncSession = Depends(get_session),
    cash: CashService = Depends(get_cash_service),
) -> RefundPaymentUseCase:
    return RefundPaymentUseCase(session, cash)


def get_list_payments_use_case(
    cash: CashRepository = Depends(get_cash_repository),
) -> ListPaymentsUseCase:
    return ListPaymentsUseCase(cash)


def get_cash_summary_use_case(
    cash: CashRepository = Depends(get_cash_repository),
) -> CashSummaryUseCase:
    return CashSummaryUseCase(cash)


def get_list_debts_use_case(
    cash: CashRepository = Depends(get_cash_repository),
) -> ListDebtsUseCase:
    return ListDebtsUseCase(cash)
