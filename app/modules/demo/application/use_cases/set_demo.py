"""Use-case: включить или выключить признак демо у клиники (команда Skrezo)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.demo.domain.models import DemoState
from app.modules.demo.infrastructure.repositories.demo_repository import DemoRepository
from app.shared.db.models.clinic_demo import ClinicDemo


class SetDemoUseCase:
    def __init__(self, session: AsyncSession, demo: DemoRepository) -> None:
        self._session = session
        self._demo = demo

    async def execute(
        self, clinic_id: uuid.UUID, *, demo: bool, confirm_name: str | None
    ) -> DemoState:
        """Переключить признак.

        Включение подтверждается названием клиники: с этого момента всё, что в ней
        появится, каждое утро удаляется, и промах строкой в списке клиентов стоил
        бы настоящей клинике пациентов. Данные до включения сброс не трогает —
        граница ставится на «сейчас»; повторное включение после выключения
        сдвигает её, чтобы не задеть появившееся, пока признак был снят.
        """
        clinic = await self._demo.clinic(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиент не найден", code="client_not_found")
        state = await self._demo.state(clinic_id)
        if demo and not clinic.demo:
            if (confirm_name or "").strip().casefold() != clinic.name.strip().casefold():
                raise ValidationAppError(
                    "Название клиники не совпадает — признак демо не включён",
                    code="confirm_name_mismatch",
                )
            since = await self._demo.db_now()
            if state is None:
                state = ClinicDemo(clinic_id=clinic_id, since=since)
                await self._demo.add_state(state)
            else:
                state.since = since
        clinic.demo = demo
        await self._session.commit()
        if state is not None:
            await self._session.refresh(state)
        return DemoState(
            clinic_id=clinic_id,
            demo=clinic.demo,
            since=state.since if state is not None else None,
            reset_at=state.reset_at if state is not None else None,
        )
