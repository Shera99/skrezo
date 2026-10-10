"""Роут: POST /leads — публичная заявка с лендинга (без OTP) в очередь заявок."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.config import settings
from app.modules.clinic_applications.application.use_cases.submit_lead import SubmitLeadUseCase
from app.modules.clinic_applications.presentation.deps import get_submit_lead_use_case
from app.modules.clinic_applications.presentation.schemas import (
    CreateLeadRequest,
    LeadCreatedResponse,
)
from app.shared.api.rate_limit import ip_rate_limit

router = APIRouter()

_rate_limit = ip_rate_limit(
    scope="lead", limit=settings.lead_rate_limit, window=settings.lead_rate_window_sec
)

_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "description": (
            "Заявку принимать не нужно. Различаются по `error`: `already_submitted` — "
            "открытая заявка по номеру уже есть, `already_client` — по номеру уже "
            "заведён аккаунт"
        ),
        "content": {
            "application/json": {
                "examples": {
                    "already_submitted": {
                        "summary": "Заявка по номеру уже есть и ещё не разобрана",
                        "description": (
                            "Не ошибка для человека: форма говорит «мы уже получили вашу "
                            "заявку». `createdAt` — время самой свежей открытой заявки."
                        ),
                        "value": {
                            "error": "already_submitted",
                            "message": "Заявка по этому номеру уже принята, мы скоро свяжемся",
                            "createdAt": "2026-10-10T09:15:00+00:00",
                        },
                    },
                    "already_client": {
                        "summary": "По номеру уже есть аккаунт",
                        "description": "Форма предлагает войти, а не оставлять заявку снова.",
                        "value": {
                            "error": "already_client",
                            "message": "По этому номеру уже есть аккаунт — войдите в приложение",
                        },
                    },
                }
            }
        },
    },
    422: {
        "description": "Номер не распознан как телефон либо тело не прошло валидацию",
        "content": {
            "application/json": {
                "examples": {
                    "invalid_phone": {
                        "summary": "Некорректный телефон",
                        "value": {
                            "error": "invalid_phone",
                            "message": "Некорректный номер телефона",
                        },
                    },
                    "validation_error": {
                        "summary": "Тело не прошло валидацию (например, имя короче двух символов)",
                        "value": {
                            "error": "validation_error",
                            "message": "Тело запроса не прошло валидацию",
                            "details": [
                                {
                                    "type": "string_too_short",
                                    "loc": ["body", "fullName"],
                                    "msg": "String should have at least 2 characters",
                                }
                            ],
                        },
                    },
                }
            }
        },
    },
    429: {
        "description": "Исчерпан лимит заявок с одного IP; `retryAfter` — секунд до сброса окна",
        "content": {
            "application/json": {
                "example": {
                    "error": "too_many_requests",
                    "message": "Слишком много попыток, попробуйте позже",
                    "retryAfter": 1800,
                }
            }
        },
    },
}


@router.post(
    "/leads",
    status_code=status.HTTP_201_CREATED,
    response_model=LeadCreatedResponse,
    summary="Оставить заявку с лендинга (без кода)",
    response_description="Идентификатор заявки в очереди и время её создания",
    responses=_ERRORS,
)
async def submit_lead(
    body: CreateLeadRequest,
    use_case: SubmitLeadUseCase = Depends(get_submit_lead_use_case),
    _rl: None = Depends(_rate_limit),
) -> LeadCreatedResponse:
    """Принять заявку с формы лендинга (публично, без токена и без OTP).

    **Одна очередь — две двери.** Лид ложится в ту же очередь, что и заявки
    `POST /clinic-applications`, и виден команде в `GET /admin/clinic-applications`
    со статусом `pending`. Отличие одно: **телефон не подтверждён**
    (`phoneVerified = false`), поэтому по такому номеру нельзя отправлять доступы.
    Название клиники, город и размер у лида необязательны — форма спрашивает
    только имя и телефон (`doctorsCountRange` попадает в заявку, лишь если это
    ровно `2-5`, `6-10` или `10+`).

    **Дедуп** идёт по нормализованному телефону, в таком порядке:

    1. по номеру уже есть аккаунт → `409 already_client` (форма предлагает войти);
    2. по номеру есть открытая заявка (`pending`) — лид или заявка через OTP →
       `409 already_submitted` с `createdAt` самой свежей из них (форма говорит
       «мы уже получили вашу заявку, скоро свяжемся» — это не ошибка для человека);
    3. иначе заявка создаётся.

    Заявки со статусом `reviewed` и `rejected` дедуп не блокируют: человек может
    прийти снова. Две одновременные отправки одного номера дают одну заявку и один
    `already_submitted`.

    **`source`** — метка трафика (`utm_source` или referrer как пришли с формы).
    Хранится отдельным полем, а не в `comment`: комментарий читает человек, а
    метка нужна, чтобы знать, какой канал приводит заявки.

    После записи команда получает уведомление в Telegram-чат; недоставка
    уведомления заявку не отменяет. Ручка ограничена по IP (429).
    """
    application = await use_case.execute(
        full_name=body.full_name,
        phone_raw=body.phone,
        company_name=body.clinic_name,
        doctors_count_range=body.doctors_count_range,
        comment=body.comment,
        source=body.source,
    )
    return LeadCreatedResponse(id=application.id, created_at=application.created_at)
