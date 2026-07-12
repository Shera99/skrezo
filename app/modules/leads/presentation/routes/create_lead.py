"""Роут: POST /leads — публичная лид-форма «Больше одного»."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.config import settings
from app.modules.leads.application.use_cases.create_lead import CreateLeadUseCase
from app.modules.leads.presentation.deps import get_create_lead_use_case
from app.modules.leads.presentation.schemas import CreateLeadRequest, LeadCreatedResponse
from app.shared.api.rate_limit import ip_rate_limit

router = APIRouter()

_rate_limit = ip_rate_limit(
    scope="lead", limit=settings.lead_rate_limit, window=settings.lead_rate_window_sec
)

_CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "Заявка с этим номером уже существует",
        "content": {
            "application/json": {
                "example": {
                    "error": "lead_already_exists",
                    "createdAt": "2026-07-05T10:00:00+00:00",
                }
            }
        },
    },
}


@router.post(
    "/leads",
    status_code=status.HTTP_201_CREATED,
    response_model=LeadCreatedResponse,
    summary="Оставить заявку «Больше одного»",
    response_description="Идентификатор и время создания заявки",
    responses=_CONFLICT,
)
async def create_lead(
    body: CreateLeadRequest,
    use_case: CreateLeadUseCase = Depends(get_create_lead_use_case),
    _rl: None = Depends(_rate_limit),
) -> LeadCreatedResponse:
    """Принять заявку клиники (публично, без токена). Дедуп по телефону."""
    lead = await use_case.execute(
        full_name=body.full_name,
        phone_raw=body.phone,
        clinic_name=body.clinic_name,
        doctors_count_range=body.doctors_count_range,
        comment=body.comment,
    )
    return LeadCreatedResponse(id=lead.id, created_at=lead.created_at)
