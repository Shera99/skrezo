"""Роут: PATCH /admin/trial-features — настроить триал-функции (только Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.access.application.use_cases.update_trial_features import (
    TrialFeatureUpdate,
    UpdateTrialFeaturesUseCase,
)
from app.modules.access.presentation.deps import get_update_trial_features_use_case
from app.modules.access.presentation.schemas import (
    TrialFeatureItem,
    TrialFeaturesResponse,
    UpdateTrialFeaturesRequest,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {
        "description": "Неизвестный ключ права",
        "content": {
            "application/json": {
                "example": {
                    "error": "permission_not_found",
                    "message": "Право не найдено: reports.view_bad",
                }
            }
        },
    },
}


@router.patch(
    "/admin/trial-features",
    response_model=TrialFeaturesResponse,
    response_description="Обновлённый каталог прав с флагами inTrial",
    summary="Настроить набор триал-функций",
    responses=_ERRORS,
)
async def update_trial_features(
    body: UpdateTrialFeaturesRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: UpdateTrialFeaturesUseCase = Depends(get_update_trial_features_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> TrialFeaturesResponse:
    """Пакетно включить/выключить права на триале.

    Применяется сразу (кеш триал-прав сбрасывается). Неизвестный ключ → 422, ни
    одно право не меняется. Действие пишется в аудит платформы.
    """
    permissions = await use_case.execute(
        [TrialFeatureUpdate(key=i.key, in_trial=i.in_trial) for i in body.items]
    )
    await audit.record(
        actor_id=actor,
        action="trial_features.updated",
        entity_type="permission",
        entity_id=None,
        payload={i.key: i.in_trial for i in body.items},
    )
    return TrialFeaturesResponse(
        items=[TrialFeatureItem.from_permission(p) for p in permissions]
    )
