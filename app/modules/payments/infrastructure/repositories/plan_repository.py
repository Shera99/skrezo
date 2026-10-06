"""Репозиторий спецпланов и заявок на условия сверх публичных тарифов."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_custom_plan import ClinicCustomPlan
from app.shared.db.models.plan_request import PlanRequest

STATUS_NEW = "new"
STATUS_DONE = "done"
STATUS_DECLINED = "declined"


@dataclass(frozen=True, slots=True)
class PlanRequestRow:
    """Заявка для очереди админки — вместе с названием клиники."""

    request: PlanRequest
    clinic_name: str


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Клиника ─────────────────────────────────────────────────────────────

    async def clinic_fresh(self, clinic_id: uuid.UUID) -> Clinic | None:
        """Клиника заново из базы: лимит мест мог поменяться, пока ждали блокировку."""
        return (
            await self._session.execute(
                select(Clinic)
                .where(Clinic.id == clinic_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    # ── Спецплан ────────────────────────────────────────────────────────────

    async def custom_plan(self, clinic_id: uuid.UUID) -> ClinicCustomPlan | None:
        return (
            await self._session.execute(
                select(ClinicCustomPlan).where(ClinicCustomPlan.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()

    async def save_custom_plan(
        self,
        clinic_id: uuid.UUID,
        *,
        title: str,
        seats: int,
        price_tiyin: int,
        note: str | None,
        set_by: uuid.UUID | None,
    ) -> ClinicCustomPlan:
        """Поставить или заменить спецплан клиники (строка одна на клинику)."""
        plan = await self.custom_plan(clinic_id)
        if plan is None:
            plan = ClinicCustomPlan(clinic_id=clinic_id)
            self._session.add(plan)
        plan.title = title
        plan.seats = seats
        plan.price_tiyin = price_tiyin
        plan.note = note
        plan.set_by = set_by
        await self._session.flush()
        return plan

    async def delete_custom_plan(self, plan: ClinicCustomPlan) -> None:
        await self._session.delete(plan)
        await self._session.flush()

    # ── Заявки ──────────────────────────────────────────────────────────────

    async def open_request(self, clinic_id: uuid.UUID) -> PlanRequest | None:
        return (
            await self._session.execute(
                select(PlanRequest).where(
                    PlanRequest.clinic_id == clinic_id, PlanRequest.status == STATUS_NEW
                )
            )
        ).scalar_one_or_none()

    async def submit_request(
        self,
        clinic_id: uuid.UUID,
        *,
        requested_by: uuid.UUID | None,
        seats: int,
        comment: str | None,
    ) -> PlanRequest:
        """Новая заявка либо обновление открытой: у клиники открыта одна."""
        request = await self.open_request(clinic_id)
        if request is None:
            request = PlanRequest(clinic_id=clinic_id, status=STATUS_NEW)
            self._session.add(request)
        request.requested_by = requested_by
        request.seats = seats
        request.comment = comment
        request.submitted_at = datetime.now(UTC)
        await self._session.flush()
        return request

    async def request(self, request_id: uuid.UUID) -> PlanRequest | None:
        return await self._session.get(PlanRequest, request_id)

    async def resolve_open(
        self, clinic_id: uuid.UUID, *, resolved_by: uuid.UUID | None, note: str
    ) -> int:
        """Закрыть открытую заявку клиники как выполненную (поставлен спецплан)."""
        result = await self._session.execute(
            update(PlanRequest)
            .where(PlanRequest.clinic_id == clinic_id, PlanRequest.status == STATUS_NEW)
            .values(
                status=STATUS_DONE,
                resolved_by=resolved_by,
                resolved_at=datetime.now(UTC),
                resolution_note=note,
            )
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def requests_page(
        self, *, status: str | None, page: int, page_size: int
    ) -> tuple[list[PlanRequestRow], int]:
        """Очередь заявок: открытые сверху, внутри — свежие первыми."""
        base = select(PlanRequest, Clinic.name).join(Clinic, Clinic.id == PlanRequest.clinic_id)
        if status is not None:
            base = base.where(PlanRequest.status == status)
        total = (
            await self._session.execute(
                select(func.count()).select_from(base.order_by(None).subquery())
            )
        ).scalar_one()
        rows = await self._session.execute(
            base.order_by((PlanRequest.status != STATUS_NEW), PlanRequest.submitted_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return [PlanRequestRow(request=r[0], clinic_name=r[1]) for r in rows], int(total)
