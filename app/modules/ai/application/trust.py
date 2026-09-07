"""Лестница доверия (Ф7): обзор ступеней и смена режима владельцем.

Вверх — только через гейт (цифры считает тот же репозиторий, что и метрики
Ф6), вниз и «выключить» — всегда. Выключение помнит заработанную ступень:
«включить обратно» возвращает её, а не сбрасывает в «Наблюдаю».
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, ValidationAppError
from app.modules.ai.domain.trust import (
    LEVELS,
    StepGate,
    TrustStats,
    gate_for,
    is_step_up,
    next_level,
)
from app.modules.ai.infrastructure.repositories.metrics_repository import (
    MetricsRepository,
    days_ago,
)
from app.shared.db.enums import AiMode
from app.shared.db.models.clinic import Clinic


@dataclass(frozen=True, slots=True)
class TrustOverview:
    mode: AiMode
    level_before_off: str | None
    changed_at: datetime | None
    reason: str | None
    stats: TrustStats
    steps: tuple[StepGate, ...]

    @property
    def enabled(self) -> bool:
        return self.mode is not AiMode.OFF

    @property
    def level(self) -> str | None:
        return self.mode.value if self.enabled else None

    @property
    def next(self) -> StepGate | None:
        """Следующий шаг: выключенному — ступень, на которую вернёт «включить»."""
        target = next_level(self.mode) if self.enabled else (self.level_before_off or "shadow")
        return next((s for s in self.steps if s.level == target), None) if target else None


class TrustLadderService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._metrics = MetricsRepository(session)

    async def overview(self, clinic: Clinic) -> TrustOverview:
        stats = await self._stats(clinic.id)
        return TrustOverview(
            mode=clinic.ai_mode,
            level_before_off=clinic.ai_mode_before_off,
            changed_at=clinic.ai_mode_changed_at,
            reason=clinic.ai_mode_reason,
            stats=stats,
            steps=tuple(self._gate(level, stats) for level in LEVELS),
        )

    async def change(
        self,
        clinic: Clinic,
        *,
        enabled: bool | None,
        level: str | None,
        reason: str = "manual",
        force: bool = False,
    ) -> tuple[AiMode, AiMode]:
        """Сменить режим; вернуть (было, стало). Коммитит.

        ``level`` задаёт ступень (и включает), ``enabled=False`` выключает,
        ``enabled=True`` без ступени возвращает заработанную до выключения.
        ``force`` — для команды Skrezo: гейт не проверяется, причина пишется.
        """
        current = clinic.ai_mode
        if level is not None:
            if level not in LEVELS:
                raise ValidationAppError(
                    "Ступень: shadow, assisted или auto", code="trust_level_unknown"
                )
            target = AiMode(level)
        elif enabled is False:
            target = AiMode.OFF
        elif enabled is True:
            target = (
                current
                if current is not AiMode.OFF
                else AiMode(clinic.ai_mode_before_off or "shadow")
            )
        else:
            raise ValidationAppError("Укажите level или enabled", code="trust_request_empty")

        if target is current:
            return current, current
        # Точка отсчёта для «вверх»: при выключенном боте — заработанная ступень.
        baseline = current if current is not AiMode.OFF else (clinic.ai_mode_before_off or "off")
        if not force and is_step_up(baseline, target):
            gate = self._gate(target.value, await self._stats(clinic.id))
            if not gate.ready:
                raise ConflictError(
                    "Ступень пока закрыта: " + "; ".join(gate.blockers),
                    code="trust_gate_closed",
                    extra={"level": target.value, "blockers": gate.blockers},
                )
        if target is AiMode.OFF and current is not AiMode.OFF:
            clinic.ai_mode_before_off = current.value
        clinic.ai_mode = target
        clinic.ai_mode_changed_at = datetime.now(UTC)
        clinic.ai_mode_reason = reason
        await self._session.commit()
        return current, target

    async def _stats(self, clinic_id: uuid.UUID) -> TrustStats:
        return TrustStats(
            shadow_replies=await self._metrics.shadow_replies_total(clinic_id),
            approvals=await self._metrics.approved_total(clinic_id),
            fact_errors=await self._metrics.fact_errors_since(
                clinic_id, days_ago(settings.ai_trust_gate_fact_error_days)
            ),
            fact_error_days=settings.ai_trust_gate_fact_error_days,
        )

    @staticmethod
    def _gate(level: str, stats: TrustStats) -> StepGate:
        return gate_for(
            level,
            stats,
            shadow_required=settings.ai_trust_gate_shadow_replies,
            approvals_required=settings.ai_trust_gate_approvals,
        )
