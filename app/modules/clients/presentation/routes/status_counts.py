"""Роут: GET /admin/clients/status-counts — число клиентов по статусам подписки."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.modules.clients.application.use_cases.status_counts import StatusCountsUseCase
from app.modules.clients.presentation.deps import get_status_counts_use_case
from app.modules.clients.presentation.schemas import StatusCountsResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.enums import SubscriptionStatus

router = APIRouter()


@router.get(
    "/admin/clients/status-counts",
    response_model=StatusCountsResponse,
    summary="Счётчики статусов клиентов",
    response_description="Число клиентов в каждом статусе подписки",
)
async def status_counts(
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: StatusCountsUseCase = Depends(get_status_counts_use_case),
) -> StatusCountsResponse:
    counts = await use_case.execute()
    return StatusCountsResponse(
        trial=counts[SubscriptionStatus.TRIAL],
        awaiting_card=counts[SubscriptionStatus.AWAITING_CARD],
        active=counts[SubscriptionStatus.ACTIVE],
        past_due=counts[SubscriptionStatus.PAST_DUE],
        blocked=counts[SubscriptionStatus.BLOCKED],
        cancelled=counts[SubscriptionStatus.CANCELLED],
    )
