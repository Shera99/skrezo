"""Use case'ы Skrezo: поставить и снять спецплан клиники.

Спецплан — итог разговора с клиникой, которой мало публичных пакетов: своё
число мест и своя цена в месяц. Места применяются сразу (это лимит
сотрудников), цена — со следующей оплаты; доплату или возврат за текущий
период команда проводит руками операцией журнала, если договорились. Открытая
заявка клиники при этом закрывается как выполненная. Снятие возвращает клинику
к публичным пакетам: лимит мест остаётся прежним, цена снова считается по ним.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.payments.infrastructure.repositories.plan_repository import PlanRepository
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.enums import ClinicType
from app.shared.db.models.clinic_custom_plan import ClinicCustomPlan


class SetCustomPlanUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._plans = PlanRepository(session)
        self._subscriptions = SubscriptionRepository(session)

    async def execute(
        self,
        clinic_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        *,
        title: str,
        seats: int,
        price_tiyin: int,
        note: str | None,
    ) -> ClinicCustomPlan:
        clinic = await self._plans.clinic_fresh(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиент не найден", code="client_not_found")
        if clinic.type is ClinicType.SOLO:
            raise ConflictError(
                "Спецплан — для клиник с сотрудниками; у соло цену задаёт тариф соло",
                code="custom_plan_clinic_only",
            )
        used, _ = await self._subscriptions.seats(clinic_id)
        if used > seats:
            raise ConflictError(
                f"Сотрудников уже {used} — мест в плане должно быть не меньше",
                code="seats_in_use",
                extra={"seatsUsed": used, "seats": seats},
            )
        plan = await self._plans.save_custom_plan(
            clinic_id,
            title=title,
            seats=seats,
            price_tiyin=price_tiyin,
            note=note,
            set_by=actor_id,
        )
        clinic.seats_limit = seats
        await self._plans.resolve_open(
            clinic_id, resolved_by=actor_id, note=f"Поставлен спецплан «{title}»"
        )
        await self._session.commit()
        return plan


class RemoveCustomPlanUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._plans = PlanRepository(session)

    async def execute(self, clinic_id: uuid.UUID) -> ClinicCustomPlan:
        plan = await self._plans.custom_plan(clinic_id)
        if plan is None:
            raise NotFoundError("Спецплана у клиента нет", code="custom_plan_not_found")
        await self._plans.delete_custom_plan(plan)
        await self._session.commit()
        return plan
