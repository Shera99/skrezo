"""Роут: GET /admin/trial-features — набор триал-функций (только Skrezo)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.modules.access.application.use_cases.get_trial_features import GetTrialFeaturesUseCase
from app.modules.access.presentation.deps import get_get_trial_features_use_case
from app.modules.access.presentation.schemas import TrialFeatureItem, TrialFeaturesResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()


@router.get(
    "/admin/trial-features",
    response_model=TrialFeaturesResponse,
    response_description="Все права каталога с флагом inTrial",
    summary="Набор функций, доступных на триале",
)
async def get_trial_features(
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetTrialFeaturesUseCase = Depends(get_get_trial_features_use_case),
) -> TrialFeaturesResponse:
    """Каталог прав с флагом ``inTrial`` — экран настройки триала в админке.

    Клиника на 30-дневном триале видит и использует только права с ``inTrial=true``;
    остальное открывается после перехода на платный тариф. Группировать по
    ``groupKey`` на фронте.
    """
    permissions = await use_case.execute()
    return TrialFeaturesResponse(
        items=[TrialFeatureItem.from_permission(p) for p in permissions]
    )
