"""Ступени доверия (Ф7) — экран «Настройки → Ассистент»."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.access.presentation.deps import require_permission
from app.modules.ai.application.trust import TrustLadderService, TrustOverview
from app.modules.ai.domain.skills import SKILL_TITLES
from app.modules.ai.domain.trust import LEVEL_TITLES
from app.modules.ai.presentation.deps import get_trust_ladder_service
from app.modules.ai.presentation.schemas import (
    SetTrustRequest,
    SkillOut,
    TrustLadderResponse,
    TrustRequirementOut,
    TrustStatsOut,
    TrustStepOut,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.shared.auth import ClientContext
from app.shared.db.models.clinic import Clinic

router = APIRouter()

_GATE_CLOSED: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "Ступень закрыта гейтом — в ответе, чего не хватает",
        "content": {
            "application/json": {
                "example": {
                    "error": "trust_gate_closed",
                    "message": (
                        "Ступень пока закрыта: одобренных ответов в режиме «Проверяю»: 153 из 200"
                    ),
                    "level": "auto",
                    "blockers": ["одобренных ответов в режиме «Проверяю»: 153 из 200"],
                }
            }
        },
    },
}


async def _clinic(context: ClientContext, session: AsyncSession) -> Clinic:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    clinic = await session.get(Clinic, context.clinic_id)
    if clinic is None:
        raise NotFoundError("Клиника не найдена", code="clinic_not_found")
    return clinic


def _response(overview: TrustOverview) -> TrustLadderResponse:
    steps = [
        TrustStepOut(
            level=step.level,
            title=LEVEL_TITLES[step.level][0],
            description=LEVEL_TITLES[step.level][1],
            current=overview.level == step.level,
            ready=step.ready,
            requirements=[
                TrustRequirementOut(
                    key=r.key, label=r.label, value=r.value, required=r.required, met=r.met
                )
                for r in step.requirements
            ],
            blockers=step.blockers,
        )
        for step in overview.steps
    ]
    nxt = overview.next
    return TrustLadderResponse(
        mode=overview.mode,
        enabled=overview.enabled,
        level=overview.level,
        level_before_off=overview.level_before_off,
        changed_at=overview.changed_at,
        reason=overview.reason,
        stats=TrustStatsOut(
            shadow_replies=overview.stats.shadow_replies,
            approvals=overview.stats.approvals,
            fact_errors=overview.stats.fact_errors,
            fact_error_days=overview.stats.fact_error_days,
        ),
        steps=steps,
        next=next((s for s in steps if nxt is not None and s.level == nxt.level), None),
        skills=[
            SkillOut(
                key=skill.key,
                title=SKILL_TITLES[skill.key][0],
                description=SKILL_TITLES[skill.key][1],
                cap=skill.cap,
                effective=skill.effective,
            )
            for skill in overview.skills
        ],
    )


@router.get(
    "/clinic/assistant/trust",
    response_model=TrustLadderResponse,
    summary="Лестница доверия: текущая ступень, условия следующей",
    response_description="Ступени с условиями и прогрессом, выключатель, кто и когда менял",
)
async def get_trust_ladder(
    context: ClientContext = Depends(require_permission("conversations.manage")),
    session: AsyncSession = Depends(get_session),
    service: TrustLadderService = Depends(get_trust_ladder_service),
) -> TrustLadderResponse:
    """Наблюдаю → Проверяю → Доверяю. Каждая ступень показывает свои условия:
    «Проверяю» — ответы, прошедшие валидатор в теневом режиме; «Доверяю» —
    одобренные ответы («153 из 200») и ноль фактических ошибок за неделю.
    Смотреть может ресепшен, менять — право `assistant.manage` (владелец всегда)."""
    clinic = await _clinic(context, session)
    return _response(await service.overview(clinic))


@router.put(
    "/clinic/assistant/trust",
    response_model=TrustLadderResponse,
    summary="Сменить ступень или выключить ассистента",
    response_description="Лестница после смены",
    responses=_GATE_CLOSED,
)
async def set_trust_ladder(
    body: SetTrustRequest,
    context: ClientContext = Depends(require_permission("assistant.manage")),
    session: AsyncSession = Depends(get_session),
    service: TrustLadderService = Depends(get_trust_ladder_service),
    audit: AuditService = Depends(get_audit_service),
) -> TrustLadderResponse:
    """`level` ставит ступень (и включает бота); вверх — только когда гейт открыт
    (иначе 409 с перечнем, чего не хватает), вниз — всегда. `enabled:false`
    выключает, помня ступень; `enabled:true` без `level` возвращает её.
    `skills` — потолки умений (матрица Ф7): `off` убирает инструменты умения у
    модели, `assisted` оставляет подбор боту, а действие — администратору
    (эскалация с карточкой вместо кнопки), `auto` — как ступень. Команда
    Skrezo может обойти гейт ручкой `/admin/clients/{id}/ai-mode`."""
    clinic = await _clinic(context, session)
    if body.enabled is not None or body.level is not None:
        was, now = await service.change(clinic, enabled=body.enabled, level=body.level)
        if was is not now:
            await audit.record_clinic(
                actor_id=context.account_id,
                clinic_id=clinic.id,
                action="assistant.trust",
                entity_type="clinic",
                entity_id=clinic.id,
                payload={"from": was.value, "to": now.value},
            )
    if body.skills:
        caps = await service.set_skills(clinic, body.skills)
        await audit.record_clinic(
            actor_id=context.account_id,
            clinic_id=clinic.id,
            action="assistant.skills",
            entity_type="clinic",
            entity_id=clinic.id,
            payload={"skills": caps},
        )
    return _response(await service.overview(clinic))
