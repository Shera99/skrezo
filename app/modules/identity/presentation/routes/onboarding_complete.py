"""Роут: POST /onboarding/complete — мастер первичной настройки пройден."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.modules.identity.application.use_cases.complete_onboarding import (
    CompleteOnboardingUseCase,
)
from app.modules.identity.presentation.deps import get_complete_onboarding_use_case
from app.modules.identity.presentation.schemas import AccountOut
from app.shared.auth import ClientContext, get_client_context

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Нет access-токена (`unauthorized`) или он невалиден (`invalid_token`)",
        "content": {
            "application/json": {
                "examples": {
                    "no_token": {
                        "summary": "Нет заголовка Authorization",
                        "value": {"error": "unauthorized", "message": "Требуется авторизация"},
                    },
                    "invalid_token": {
                        "summary": "Токен невалиден, истёк или это refresh-токен",
                        "value": {"error": "invalid_token", "message": "Невалидный токен"},
                    },
                }
            }
        },
    },
    403: {
        "description": "Аккаунт заблокирован командой Skrezo",
        "content": {
            "application/json": {
                "example": {"error": "account_blocked", "message": "Аккаунт заблокирован"}
            }
        },
    },
    404: {
        "description": "Аккаунт токена не найден",
        "content": {
            "application/json": {
                "example": {"error": "account_not_found", "message": "Аккаунт не найден"}
            }
        },
    },
}


@router.post(
    "/complete",
    response_model=AccountOut,
    summary="Отметить, что мастер настройки пройден",
    response_description="Аккаунт с `onboarded=true` (тот же объект, что `account` в `/auth/me`)",
    responses=_ERRORS,
)
async def complete_onboarding(
    context: ClientContext = Depends(get_client_context),
    use_case: CompleteOnboardingUseCase = Depends(get_complete_onboarding_use_case),
) -> AccountOut:
    """Сообщить серверу, что мастер первичной настройки клиники пройден.

    Приложение зовёт ручку по завершении мастера. Сервер ставит аккаунту флаг
    `onboarded=true`, и при повторном входе или входе с другого телефона флаг
    приходит в `account.onboarded` (`/auth/me`, `/auth/otp/verify`), так что
    мастер заново не начинается.

    Тело запроса не нужно: пустое тело и `{}` равнозначны, содержимое
    игнорируется.

    **Идемпотентна.** Если флаг уже стоит, ничего не пишется, ответ тот же —
    повторный вызов безопасен (повтор после обрыва связи, двойное нажатие).

    **Проверок заполненности нет — намеренно.** Город, специализация, график и
    прайс не проверяются, ручка не отвечает 422: приложение молча глотает
    ошибки этой ручки, и блокирующая проверка вернула бы тот самый баг —
    мастер начинался бы заново после каждого входа. Обратного сброса флага через
    клиентскую ручку нет.

    Флаг лежит на **аккаунте**, а не на клинике, поэтому он общий для всех
    клиник человека, а клиника в токене не требуется.
    """
    account = await use_case.execute(context.account_id)
    return AccountOut.model_validate(account)
