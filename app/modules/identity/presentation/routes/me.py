"""Роут: GET /auth/me — профиль текущего клиента и его права."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.identity.application.use_cases.get_me import GetMeUseCase
from app.modules.identity.presentation.deps import get_get_me_use_case
from app.modules.identity.presentation.schemas import AccountOut, ClinicMembershipOut, MeResponse
from app.shared.auth import ClientContext, get_client_context

router = APIRouter()


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Текущий клиент (аккаунт, активная клиника, права)",
)
async def me(
    context: ClientContext = Depends(get_client_context),
    use_case: GetMeUseCase = Depends(get_get_me_use_case),
) -> MeResponse:
    result = await use_case.execute(context)
    return MeResponse(
        account=AccountOut.model_validate(result.account),
        active_clinic=(ClinicMembershipOut.from_context(result.active) if result.active else None),
        permissions=result.permissions,
        clinics=[ClinicMembershipOut.from_context(m) for m in result.memberships],
        is_platform_admin=result.is_platform_admin,
    )
