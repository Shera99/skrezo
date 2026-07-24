"""Роут: POST /auth/solo/register — регистрация соло-врача (шаг 3, анкета)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.modules.identity.application.use_cases.register_solo import RegisterSoloUseCase
from app.modules.identity.presentation.deps import get_register_solo_use_case
from app.modules.identity.presentation.schemas import ClientTokenResponse, SoloRegisterRequest

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "Регистрация не подтверждена (нет/истёк билет OTP)",
        "content": {
            "application/json": {
                "example": {
                    "error": "registration_not_verified",
                    "message": "Регистрация не подтверждена или истекла",
                }
            }
        },
    },
    409: {
        "description": "Этот номер уже используется",
        "content": {
            "application/json": {
                "example": {
                    "error": "phone_already_used",
                    "message": "Этот номер уже используется",
                }
            }
        },
    },
    422: {
        "description": "Неизвестный город или специализация",
        "content": {
            "application/json": {
                "example": {
                    "error": "specialization_not_found",
                    "message": "Специализация не найдена",
                }
            }
        },
    },
}


@router.post(
    "/solo/register",
    response_model=ClientTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация соло-врача",
    response_description="Токены и профиль — соло-врач сразу вошёл (30-дневный триал)",
    responses=_ERRORS,
)
async def solo_register(
    body: SoloRegisterRequest,
    use_case: RegisterSoloUseCase = Depends(get_register_solo_use_case),
) -> ClientTokenResponse:
    """Шаг 3: имя + профессия + город. Телефон берётся из билета OTP, не из тела.

    Создаёт соло-аккаунт (passwordless — дальше вход по OTP), клинику типа
    ``solo``, членство владельца со специализацией и 30-дневный триал, затем сразу
    выдаёт токены. Набор функций на триале ограничен списком из админки.
    """
    result = await use_case.execute(
        body.registration_token, body.full_name, body.specialization_id, body.city_id
    )
    return ClientTokenResponse.from_login_result(result)
