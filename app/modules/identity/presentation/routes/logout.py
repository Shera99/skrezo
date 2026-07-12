"""Роут: POST /auth/logout — выход клиента (отзыв refresh)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.identity.application.use_cases.logout import LogoutUseCase
from app.modules.identity.presentation.deps import get_logout_use_case
from app.modules.identity.presentation.schemas import RefreshRequest
from app.shared.api.schemas import OkResponse

router = APIRouter()


@router.post(
    "/logout",
    response_model=OkResponse,
    summary="Выход клиента",
)
async def logout(
    body: RefreshRequest,
    use_case: LogoutUseCase = Depends(get_logout_use_case),
) -> OkResponse:
    await use_case.execute(body.refresh_token)
    return OkResponse()
