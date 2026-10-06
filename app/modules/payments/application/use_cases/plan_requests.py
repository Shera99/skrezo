"""Use case'ы заявок на условия сверх публичных тарифов.

Клиника пишет, сколько мест ей нужно, — команда Skrezo получает сообщение в
чат и видит заявку в очереди админки. Открытая заявка у клиники одна: повтор
обновляет её. Закрывается заявка решением Skrezo или сама — когда клинике
ставят спецплан.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.payments.application.services.pricing import SubscriptionPricing
from app.modules.payments.infrastructure.repositories.plan_repository import (
    STATUS_NEW,
    PlanRepository,
    PlanRequestRow,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.plan_request import PlanRequest
from app.shared.notifications.notifier import Notifier

logger = structlog.get_logger(__name__)


class SubmitPlanRequestUseCase:
    def __init__(self, session: AsyncSession, notifier: Notifier | None = None) -> None:
        self._session = session
        self._plans = PlanRepository(session)
        self._subscriptions = SubscriptionRepository(session)
        self._notifier = notifier

    async def execute(
        self,
        clinic_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        *,
        seats: int,
        comment: str | None,
    ) -> PlanRequest:
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        request = await self._plans.submit_request(
            clinic_id, requested_by=actor_id, seats=seats, comment=comment
        )
        await self._session.commit()
        await self._notify(clinic, actor_id, request)
        return request

    async def _notify(
        self, clinic: Clinic, actor_id: uuid.UUID | None, request: PlanRequest
    ) -> None:
        if self._notifier is None:
            return
        used, limit = await self._subscriptions.seats(clinic.id)
        try:
            card = await SubscriptionPricing(self._session).card(clinic.id)
            plan = (
                card.custom_plan.title
                if card.custom_plan is not None
                else (card.current_package or "сверх пакетов")
            )
        except ConflictError, NotFoundError:
            plan = "тариф не настроен"
        who = (
            (
                await self._session.execute(
                    select(Account.full_name, Account.phone).where(Account.id == actor_id)
                )
            ).one_or_none()
            if actor_id is not None
            else None
        )
        lines = [
            f"Заявка на расширение: «{clinic.name}» хочет мест: {request.seats}",
            f"Сейчас: {plan}, занято {used} из {limit if limit is not None else '—'}",
        ]
        if request.comment:
            lines.append(f"Комментарий: {request.comment}")
        if who is not None:
            lines.append(f"Отправил: {who[0]}, {who[1]}")
        await self._notifier.notify_team("\n".join(lines), event="plan_request")
        logger.info("plan_request_submitted", clinic_id=str(clinic.id), seats=request.seats)


class ListPlanRequestsUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self._plans = PlanRepository(session)

    async def execute(
        self, *, status: str | None, page: int, page_size: int
    ) -> tuple[list[PlanRequestRow], int]:
        return await self._plans.requests_page(status=status, page=page, page_size=page_size)


class ResolvePlanRequestUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._plans = PlanRepository(session)

    async def execute(
        self,
        request_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        *,
        status: str,
        note: str | None,
    ) -> PlanRequestRow:
        request = await self._plans.request(request_id)
        if request is None:
            raise NotFoundError("Заявка не найдена", code="plan_request_not_found")
        if request.status != STATUS_NEW:
            raise ConflictError("Заявка уже закрыта", code="plan_request_closed")
        request.status = status
        request.resolution_note = note
        request.resolved_by = actor_id
        request.resolved_at = datetime.now(UTC)
        await self._session.commit()
        clinic = await self._session.get(Clinic, request.clinic_id)
        return PlanRequestRow(request=request, clinic_name=clinic.name if clinic else "")
