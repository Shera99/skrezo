"""Роут: POST /auth/refresh — обновление клиентского access-токена."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.identity.application.use_cases.refresh_token import RefreshTokenUseCase
from app.modules.identity.presentation.deps import get_refresh_token_use_case
from app.modules.identity.presentation.schemas import AccessResponse, RefreshRequest

router = APIRouter()


@router.post(
    "/refresh",
    response_model=AccessResponse,
    summary="Обновление access-токена клиента",
)
async def refresh(
    body: RefreshRequest,
    use_case: RefreshTokenUseCase = Depends(get_refresh_token_use_case),
) -> AccessResponse:
    access, expires_in = await use_case.execute(body.refresh_token)
    return AccessResponse(access_token=access, expires_in=expires_in)
