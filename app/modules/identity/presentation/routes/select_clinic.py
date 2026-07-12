"""Роут: POST /auth/select-clinic — выбор активной клиники."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.identity.application.use_cases.select_clinic import SelectClinicUseCase
from app.modules.identity.presentation.deps import get_select_clinic_use_case
from app.modules.identity.presentation.schemas import ClientTokensOut, SelectClinicRequest
from app.shared.auth import ClientContext, get_client_context

router = APIRouter()


@router.post(
    "/select-clinic",
    response_model=ClientTokensOut,
    summary="Выбрать активную клинику",
    response_description="Новые токены в контексте выбранной клиники",
)
async def select_clinic(
    body: SelectClinicRequest,
    context: ClientContext = Depends(get_client_context),
    use_case: SelectClinicUseCase = Depends(get_select_clinic_use_case),
) -> ClientTokensOut:
    """Выдать токены, привязанные к выбранной клинике (для мультиклиничных аккаунтов)."""
    tokens = await use_case.execute(
        context.account_id, body.clinic_id, platform_admin=context.is_platform_admin
    )
    return ClientTokensOut(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )
