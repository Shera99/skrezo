"""Роут: PATCH /auth/me — сотрудник правит свой профиль."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.modules.identity.application.use_cases.update_profile import UpdateProfileUseCase
from app.modules.identity.presentation.deps import get_update_profile_use_case
from app.modules.identity.presentation.schemas import AccountOut, UpdateProfileRequest
from app.shared.auth import ClientContext, get_client_context

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {
        "description": "Недопустимое поле или явный null в обязательном",
        "content": {
            "application/json": {
                "example": {
                    "error": "validation_error",
                    "message": "Поля нельзя обнулить: fullName",
                }
            }
        },
    },
}


@router.patch(
    "/me",
    response_model=AccountOut,
    summary="Изменить свой профиль",
    response_description="Профиль после правки",
    responses=_ERRORS,
)
async def update_profile(
    body: UpdateProfileRequest,
    context: ClientContext = Depends(get_client_context),
    use_case: UpdateProfileUseCase = Depends(get_update_profile_use_case),
) -> AccountOut:
    """Своё имя и язык интерфейса — без участия администратора.

    Телефон здесь не меняется: он же логин, и правит его владелец или менеджер
    через `PUT /clinic/staff/{id}`. Иначе работник мог бы увести доступ к
    аккаунту у клиники.

    Аккаунт **глобальный**: врач, работающий в нескольких клиниках, меняет имя
    сразу везде — человек один, и разные имена в разных клиниках путали бы и
    расписание, и журнал действий.
    """
    account = await use_case.execute(
        context.account_id, body.model_dump(exclude_unset=True)
    )
    return AccountOut.model_validate(account)
