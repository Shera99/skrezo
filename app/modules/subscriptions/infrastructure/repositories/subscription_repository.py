"""Репозиторий подписок (запись; коммит — на стороне транзакции провижининга)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.subscriptions.domain.models import SubscriptionContact
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.role import Role
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.subscription_event import SubscriptionEvent


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_clinic(self, clinic_id: uuid.UUID) -> Subscription | None:
        """Подписка клиники (одна активная запись на клинику)."""
        return (
            await self._session.execute(
                select(Subscription).where(Subscription.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()

    async def create(
        self,
        *,
        clinic_id: uuid.UUID,
        status: SubscriptionStatus,
        trial_started_at: datetime,
        trial_ends_at: datetime,
        card_attach_deadline: datetime | None = None,
    ) -> Subscription:
        subscription = Subscription(
            clinic_id=clinic_id,
            status=status,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            card_attach_deadline=card_attach_deadline,
        )
        self._session.add(subscription)
        await self._session.flush()
        return subscription

    async def expire_trials(self, now: datetime) -> list[SubscriptionContact]:
        """Перевести триалы с истёкшим сроком в ``trial_expired``.

        Один UPDATE с ``RETURNING`` вместо «выбрать и обновить по одной»: клиник
        может быть много, а условие ``status = 'trial'`` делает операцию
        идемпотентной — повторный прогон ничего не найдёт. Из него же берётся
        список, кому писать: перешедших видит только этот запрос.
        """
        rows = (
            await self._session.execute(
                update(Subscription)
                .where(
                    Subscription.status == SubscriptionStatus.TRIAL,
                    Subscription.trial_ends_at <= now,
                )
                .values(status=SubscriptionStatus.TRIAL_EXPIRED)
                .returning(
                    Subscription.id,
                    Subscription.clinic_id,
                    Subscription.trial_ends_at.label("deadline"),
                )
            )
        ).all()
        return await self._with_owners(rows)

    async def expire_paid_periods(self, now: datetime) -> list[SubscriptionContact]:
        """Перевести оплаченные подписки с истёкшим периодом в ``past_due``.

        Зеркало :meth:`expire_trials` и с той же оговоркой: доступ закрывает не
        этот запрос, а политика подписки по дате ``current_period_end``. Статус
        нужен, чтобы админка, дашборд и отчёты видели должника.

        Условие ``current_period_end IS NOT NULL`` обязательно: ``active`` без
        даты конца периода — запись, сделанная руками, и трогать её джоба не
        должна (``NULL <= now`` в SQL и так не выполнится, но условие говорит об
        этом вслух).
        """
        rows = (
            await self._session.execute(
                update(Subscription)
                .where(
                    Subscription.status == SubscriptionStatus.ACTIVE,
                    Subscription.current_period_end.is_not(None),
                    Subscription.current_period_end <= now,
                )
                .values(status=SubscriptionStatus.PAST_DUE)
                .returning(
                    Subscription.id,
                    Subscription.clinic_id,
                    Subscription.current_period_end.label("deadline"),
                )
            )
        ).all()
        return await self._with_owners(rows)

    async def activate(
        self, clinic_id: uuid.UUID, *, period_start: datetime, period_end: datetime
    ) -> Subscription | None:
        """Отметить оплату: подписка становится ``active`` до ``period_end``.

        Возвращает подписку (или ``None``, если её нет). Событие перехода пишет
        вызывающий use-case — ему видно, из какого статуса пришли.
        """
        subscription = await self.by_clinic(clinic_id)
        if subscription is None:
            return None
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.current_period_start = period_start
        subscription.current_period_end = period_end
        subscription.cancelled_at = None
        await self._session.flush()
        return subscription

    async def set_status(
        self, clinic_id: uuid.UUID, status: SubscriptionStatus
    ) -> Subscription | None:
        """Перевести подписку в статус вручную (блок Skrezo)."""
        subscription = await self.by_clinic(clinic_id)
        if subscription is None:
            return None
        subscription.status = status
        await self._session.flush()
        return subscription

    async def claim_trial_warnings(
        self, now: datetime, until: datetime
    ) -> list[SubscriptionContact]:
        """Забрать клиники, которым пора сказать «триал скоро кончится».

        «Забрать», а не «выбрать»: отметка ``trial_warning_sent_at`` ставится тем
        же UPDATE, что и отбирает строки. Разделённые SELECT и UPDATE дали бы
        второе предупреждение, если джоба запустилась дважды (ретрай, второй
        воркер) — а условие ``IS NULL`` внутри одного UPDATE этого не допустит.
        """
        rows = (
            await self._session.execute(
                update(Subscription)
                .where(
                    Subscription.status == SubscriptionStatus.TRIAL,
                    Subscription.trial_warning_sent_at.is_(None),
                    Subscription.trial_ends_at > now,
                    Subscription.trial_ends_at <= until,
                )
                .values(trial_warning_sent_at=now)
                .returning(
                    Subscription.id,
                    Subscription.clinic_id,
                    Subscription.trial_ends_at.label("deadline"),
                )
            )
        ).all()
        return await self._with_owners(rows)

    async def _with_owners(self, rows: Sequence[Row[Any]]) -> list[SubscriptionContact]:
        """Дополнить строки подписок названием клиники и телефоном владельца.

        Владелец подцепляется LEFT JOIN'ом: клиника без активного владельца
        (такого быть не должно, но бывает после архивации) не должна пропадать
        из результата — иначе джоба молча не заметит её вовсе.
        """
        if not rows:
            return []
        owner = (
            select(
                Membership.clinic_id.label("clinic_id"),
                Membership.account_id.label("owner_account_id"),
            )
            .join(Role, Role.id == Membership.role_id)
            .where(Membership.active.is_(True), Role.is_owner.is_(True))
            .subquery()
        )
        clinic_ids = [row.clinic_id for row in rows]
        contacts = (
            await self._session.execute(
                select(Clinic.id, Clinic.name, Account.phone)
                .select_from(Clinic)
                .outerjoin(owner, owner.c.clinic_id == Clinic.id)
                .outerjoin(Account, Account.id == owner.c.owner_account_id)
                .where(Clinic.id.in_(clinic_ids))
            )
        ).all()
        by_clinic = {row[0]: (row[1], row[2]) for row in contacts}
        return [
            SubscriptionContact(
                subscription_id=row.id,
                clinic_id=row.clinic_id,
                clinic_name=by_clinic.get(row.clinic_id, ("", None))[0],
                deadline=row.deadline,
                owner_phone=by_clinic.get(row.clinic_id, ("", None))[1],
            )
            for row in rows
        ]

    async def add_event(
        self,
        *,
        subscription_id: uuid.UUID,
        from_status: SubscriptionStatus | None,
        to_status: SubscriptionStatus,
        reason: str | None = None,
    ) -> None:
        self._session.add(
            SubscriptionEvent(
                subscription_id=subscription_id,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
        )
        await self._session.flush()
