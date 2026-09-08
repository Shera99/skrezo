"""Журнал операций и кошелёк клиники (истина о деньгах клиники у Skrezo).

Любое движение: заблокировать строку кошелька → строка журнала с остатком
после операции → новый остаток кошелька. Всё в транзакции вызывающего —
коммит делает он, вместе с тем, ради чего деньги двигались (открытие
периода, отметка платежа). Повторный колбэк провайдера упирается в
``idempotency_key`` и возвращает уже проведённую строку.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import PaymentRequiredError
from app.modules.payments.application.services.pricing import PriceQuote, SubscriptionPricing
from app.modules.payments.domain.ledger import (
    KIND_CREDIT,
    KIND_DEBIT,
    SOURCE_CHARGE,
    InsufficientFundsError,
    next_balance,
)
from app.modules.payments.domain.periods import next_period
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_ledger_entry import ClinicLedgerEntry
from app.shared.db.models.clinic_wallet import ClinicWallet
from app.shared.db.models.subscription import Subscription

logger = structlog.get_logger(__name__)


class LedgerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Кошелёк ─────────────────────────────────────────────────────────────

    async def wallet(self, clinic_id: uuid.UUID, *, lock: bool = False) -> ClinicWallet:
        """Кошелёк клиники; создаётся при первом обращении. ``lock`` — FOR UPDATE."""
        stmt = select(ClinicWallet).where(ClinicWallet.clinic_id == clinic_id)
        if lock:
            stmt = stmt.with_for_update()
        wallet = (await self._session.execute(stmt)).scalar_one_or_none()
        if wallet is None:
            wallet = ClinicWallet(clinic_id=clinic_id, balance_tiyin=0, autopay_enabled=True)
            self._session.add(wallet)
            await self._session.flush()
            if lock:
                wallet = (await self._session.execute(stmt)).scalar_one()
            else:
                # Чтение без транзакции вызывающего: пустой кошелёк фиксируем сразу,
                # иначе он не переживёт запрос.
                await self._session.commit()
        return wallet

    async def set_autopay(self, clinic_id: uuid.UUID, enabled: bool) -> ClinicWallet:
        wallet = await self.wallet(clinic_id, lock=True)
        wallet.autopay_enabled = enabled
        await self._session.commit()
        return wallet

    # ── Журнал ──────────────────────────────────────────────────────────────

    async def post(
        self,
        clinic_id: uuid.UUID,
        *,
        kind: str,
        source: str,
        amount_tiyin: int,
        description: str,
        payment_id: uuid.UUID | None = None,
        period: tuple[datetime, datetime] | None = None,
        provider_payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> ClinicLedgerEntry:
        """Провести операцию; с тем же ``idempotency_key`` — вернуть уже проведённую.

        :raises InsufficientFundsError: расход больше остатка (кроме возврата и корректировки).
        """
        if idempotency_key:
            existing = (
                await self._session.execute(
                    select(ClinicLedgerEntry).where(
                        ClinicLedgerEntry.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
        wallet = await self.wallet(clinic_id, lock=True)
        balance = next_balance(wallet.balance_tiyin, kind, amount_tiyin, source=source)
        entry = ClinicLedgerEntry(
            clinic_id=clinic_id,
            kind=kind,
            source=source,
            amount_tiyin=amount_tiyin,
            balance_after_tiyin=balance,
            payment_id=payment_id,
            period_start=period[0] if period else None,
            period_end=period[1] if period else None,
            description=description,
            provider_payload=provider_payload,
            idempotency_key=idempotency_key,
            created_by=created_by,
            # Своё время, а не now() базы: у строк одной транзакции оно совпало бы,
            # и приход со списанием менялись бы местами в выписке.
            created_at=datetime.now(UTC),
        )
        wallet.balance_tiyin = balance
        self._session.add(entry)
        await self._session.flush()
        logger.info(
            "clinic_ledger_posted",
            clinic_id=str(clinic_id),
            kind=kind,
            source=source,
            amount=amount_tiyin,
            balance=balance,
        )
        return entry

    async def entries(
        self, clinic_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[ClinicLedgerEntry], int]:
        from sqlalchemy import func

        base = select(ClinicLedgerEntry).where(ClinicLedgerEntry.clinic_id == clinic_id)
        total = (
            await self._session.execute(
                select(func.count()).select_from(base.order_by(None).subquery())
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                base.order_by(ClinicLedgerEntry.created_at.desc(), ClinicLedgerEntry.id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        ).scalars()
        return list(rows), int(total)

    # ── Подписка с баланса ──────────────────────────────────────────────────

    async def charge_subscription(
        self,
        clinic_id: uuid.UUID,
        months: int,
        *,
        manage: ManageSubscriptionUseCase,
        payment_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
        reason: str,
    ) -> tuple[ClinicLedgerEntry, PriceQuote]:
        """Списать цену за ``months`` и открыть период от конца текущего.

        Коммитит (через продление подписки). Не хватает — 402
        ``insufficient_balance`` с недостачей; журнал не трогается.
        """
        quote = await SubscriptionPricing(self._session).quote(clinic_id, months)
        subscription = await SubscriptionRepository(self._session).by_clinic(clinic_id)
        now = datetime.now(UTC)
        current_end = (
            subscription.current_period_end
            if subscription is not None and subscription.status is SubscriptionStatus.ACTIVE
            else None
        )
        start, end = next_period(now, current_end, months)
        clinic = await self._session.get(Clinic, clinic_id)
        try:
            entry = await self.post(
                clinic_id,
                kind=KIND_DEBIT,
                source=SOURCE_CHARGE,
                amount_tiyin=quote.total_tiyin,
                description=(
                    f"Подписка «{clinic.name if clinic else clinic_id}»: {months} мес., "
                    f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"
                ),
                payment_id=payment_id,
                period=(start, end),
                created_by=created_by,
            )
        except InsufficientFundsError as exc:
            raise PaymentRequiredError(
                "На балансе не хватает на продление — пополните баланс",
                code="insufficient_balance",
                extra={
                    "balanceTiyin": exc.balance_tiyin,
                    "amountTiyin": exc.amount_tiyin,
                    "shortfallTiyin": exc.shortfall_tiyin,
                },
            ) from exc
        await manage.activate(clinic_id, paid_until=end, period_start=start, reason=reason)
        return entry, quote

    async def try_autorenew(
        self, clinic_id: uuid.UUID, *, manage: ManageSubscriptionUseCase, reason: str
    ) -> ClinicLedgerEntry | None:
        """Продлить на месяц с баланса, если автопродление включено и денег хватает.

        Зовётся после пополнения и ночным заданием. Тишина, а не ошибка, когда
        продлевать нечего: подписка в силе и до конца далеко, автопродление
        выключено, баланса мало.
        """
        wallet = await self.wallet(clinic_id)
        if not wallet.autopay_enabled or wallet.balance_tiyin <= 0:
            return None
        subscription = await SubscriptionRepository(self._session).by_clinic(clinic_id)
        if subscription is None or not subscription_due(subscription):
            return None
        quote = await SubscriptionPricing(self._session).quote(clinic_id, 1)
        if wallet.balance_tiyin < quote.monthly_tiyin:
            return None
        entry, _ = await self.charge_subscription(clinic_id, 1, manage=manage, reason=reason)
        return entry

    @staticmethod
    def credit_kind() -> str:
        return KIND_CREDIT


def subscription_due(subscription: Subscription, now: datetime | None = None) -> bool:
    """Пора ли продлевать: период не открыт, истёк или кончается в ближайшие дни.

    Единое правило для пополнения и ночного задания: действующая подписка с
    запасом больше ``AUTOPAY_DAYS_BEFORE`` дней денег с баланса не берёт.
    """
    now = now or datetime.now(UTC)
    if subscription.status is not SubscriptionStatus.ACTIVE:
        return True
    end = subscription.current_period_end
    return end is None or end <= now + timedelta(days=settings.autopay_days_before)
