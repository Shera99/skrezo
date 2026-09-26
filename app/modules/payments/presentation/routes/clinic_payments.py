"""Роуты кабинета: оплата подписки через платёжную страницу FreedomPay."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.payments.application.use_cases.start_subscription_payment import (
    StartSubscriptionPaymentUseCase,
)
from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.modules.payments.presentation.deps import (
    get_payment_repository,
    get_start_payment_use_case,
)
from app.modules.payments.presentation.schemas import (
    ClinicPaymentOut,
    ClinicPaymentsResponse,
    PriceQuoteOut,
    StartPaymentRequest,
)
from app.shared.auth import ClientContext

router = APIRouter()

_START_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "description": (
            "Онлайн-оплата не подключена, тариф не настроен или подписку открывает только "
            "Skrezo: ручной блок (`subscription_blocked`) и уход клиента "
            "(`subscription_cancelled`) оплатой подписки не снимаются. Пополнение баланса "
            "при этом доступно — деньги лягут на баланс"
        ),
        "content": {
            "application/json": {
                "examples": {
                    "payments_not_configured": {
                        "summary": "Онлайн-оплата не подключена",
                        "value": {
                            "error": "payments_not_configured",
                            "message": "Онлайн-оплата пока не подключена — напишите в Skrezo",
                        },
                    },
                    "subscription_blocked": {
                        "summary": "Подписка заблокирована Skrezo",
                        "value": {
                            "error": "subscription_blocked",
                            "message": (
                                "Подписка заблокирована Skrezo — оплата её не откроет, напишите нам"
                            ),
                        },
                    },
                }
            }
        },
    },
    502: {
        "description": "Платёжный сервис не принял запрос",
        "content": {
            "application/json": {
                "example": {
                    "error": "payment_init_failed",
                    "message": "Платёжный сервис не принял запрос — попробуйте позже",
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
    "/clinic/subscription/price",
    response_model=PriceQuoteOut,
    summary="Цена подписки по тарифу за N месяцев",
    responses={409: _START_ERRORS[409]},
)
async def subscription_price(
    months: int = Query(default=1, ge=1, le=12),
    context: ClientContext = Depends(require_permission("billing.view")),
    use_case: StartSubscriptionPaymentUseCase = Depends(get_start_payment_use_case),
) -> PriceQuoteOut:
    """Соло — фиксированная цена, клиника — пакет по числу мест (лимит по тарифу
    или занятые места, если он не задан). `available=false` — реквизиты провайдера
    ещё не заданы, оплата пока по счёту через Skrezo."""
    quote = await use_case.quote(_clinic(context), months)
    return PriceQuoteOut(
        monthly_tiyin=quote.monthly_tiyin,
        months=quote.months,
        total_tiyin=quote.total_tiyin,
        seats=quote.seats,
        available=use_case.available,
    )


@router.post(
    "/clinic/subscription/payments",
    status_code=status.HTTP_201_CREATED,
    response_model=ClinicPaymentOut,
    summary="Начать оплату подписки — получить страницу оплаты FreedomPay",
    response_description="Платёж со ссылкой `redirectUrl` — отправьте владельца на неё",
    responses=_START_ERRORS,
)
async def start_payment(
    body: StartPaymentRequest,
    context: ClientContext = Depends(require_permission("billing.manage")),
    use_case: StartSubscriptionPaymentUseCase = Depends(get_start_payment_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ClinicPaymentOut:
    """`months` — оплата подписки по тарифу, `amountTiyin` — пополнение баланса
    (не меньше `TOPUP_MIN_TIYIN`). Владелец уходит на страницу оплаты и
    возвращается на `{CABINET_URL}/pay?payment=<id>` — и при успехе, и при
    отказе: страница спрашивает итог по id. `returnMode`: `auto` — сразу, без
    кнопки (компьютер), `button` или ничего — кнопкой «Вернуться» (телефон).

    Деньги проводит только серверный колбэк провайдера: оплата подписки тут же
    списывает цену и открывает период, пополнение остаётся на балансе (и
    продлевает подписку, если она ждёт денег). Оплата на пробном периоде не
    сжигает оставшиеся дни — оплаченный период начнётся с конца триала.
    Состояние — в `GET /clinic/subscription/payments/{id}`."""
    clinic_id = _clinic(context)
    payment = await use_case.execute(
        clinic_id,
        actor_id=context.account_id,
        months=body.months,
        topup_tiyin=body.amount_tiyin,
        return_mode=body.return_mode,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="subscription.payment_started",
        entity_type="platform_payment",
        entity_id=payment.id,
        payload={
            "purpose": payment.purpose,
            "months": payment.months,
            "amountTiyin": payment.amount_tiyin,
        },
    )
    return ClinicPaymentOut.from_orm_payment(payment)


@router.get(
    "/clinic/subscription/payments",
    response_model=ClinicPaymentsResponse,
    summary="Платежи клиники за подписку",
)
async def list_payments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    context: ClientContext = Depends(require_permission("billing.view")),
    repository: PlatformPaymentRepository = Depends(get_payment_repository),
) -> ClinicPaymentsResponse:
    """Новые сверху; у ожидающих — ссылка на страницу оплаты."""
    rows, total = await repository.list_for_clinic(_clinic(context), page=page, page_size=page_size)
    return ClinicPaymentsResponse(
        list=[ClinicPaymentOut.from_orm_payment(p) for p in rows],
        current_page=page,
        page_size=page_size,
        total_count=total,
    )


@router.get(
    "/clinic/subscription/payments/{payment_id}",
    response_model=ClinicPaymentOut,
    summary="Состояние платежа",
    responses={404: {"description": "Платёж не найден"}},
)
async def get_payment(
    payment_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("billing.view")),
    repository: PlatformPaymentRepository = Depends(get_payment_repository),
) -> ClinicPaymentOut:
    """Опрашивайте после возврата со страницы оплаты: `paid` — период открыт."""
    payment = await repository.get_for_clinic(_clinic(context), payment_id)
    if payment is None:
        raise NotFoundError("Платёж не найден", code="payment_not_found")
    return ClinicPaymentOut.from_orm_payment(payment)
