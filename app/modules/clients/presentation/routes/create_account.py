"""Роут: POST /admin/clients — создать аккаунт клиента (клиника/соло)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.application.use_cases.create_account import CreateAccountUseCase
from app.modules.clients.presentation.deps import get_create_account_use_case
from app.modules.clients.presentation.schemas import AccountCreatedResponse, CreateAccountRequest
from app.modules.clinics.application.services.provisioning_service import ProvisionCommand
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.enums import ClinicApplicationStatus

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "description": (
            "Телефон владельца уже используется (в сообщении — имя владельца) либо по "
            "заявке `applicationId` клиент уже заведён (`clinicId` — созданная ранее "
            "клиника). Телефон проверяется раньше заявки, поэтому повтор уже выполненного "
            "запроса с тем же телефоном отвечает `phone_already_used`, а не "
            "`application_already_converted`"
        ),
        "content": {
            "application/json": {
                "examples": {
                    "phone_already_used": {
                        "summary": "Телефон владельца уже занят",
                        "description": (
                            "Проверяется первым, раньше заявки: так отвечает и повтор "
                            "запроса, который уже выполнен (тот же телефон владельца). "
                            "`clinicId` в этом ответе нет."
                        ),
                        "value": {"error": "phone_already_used", "message": "..."},
                    },
                    "application_already_converted": {
                        "summary": "По заявке уже заведён клиент",
                        "description": (
                            "Заявку нельзя конвертировать дважды: ничего не создаётся, в "
                            "ответе — клиника, заведённая первой. Приходит, когда телефон "
                            "владельца другой, либо когда запрос успел пройти проверку "
                            "телефона до коммита первого (два одновременных запроса)."
                        ),
                        "value": {
                            "error": "application_already_converted",
                            "message": "По этой заявке клиент уже заведён",
                            "clinicId": "7d2f4a6c-8e0b-4c1d-9a3e-5b7c9d1e3f50",
                        },
                    },
                }
            }
        },
    },
    422: {
        "description": (
            "Заявка `applicationId`, город или специализация не найдены, либо тело "
            "не прошло валидацию. Клиент и заявка не меняются"
        ),
        "content": {
            "application/json": {
                "examples": {
                    "application_not_found": {
                        "summary": "Заявки с таким applicationId нет",
                        "value": {
                            "error": "application_not_found",
                            "message": "Заявка не найдена",
                        },
                    },
                    "city_not_found": {
                        "summary": "Города нет в справочнике",
                        "value": {"error": "city_not_found", "message": "Город не найден"},
                    },
                    "specialization_not_found": {
                        "summary": "Специализации нет в справочнике",
                        "value": {
                            "error": "specialization_not_found",
                            "message": "Специализация не найдена",
                        },
                    },
                    "validation_error": {
                        "summary": "Тело не прошло валидацию (например, applicationId не UUID)",
                        "value": {
                            "error": "validation_error",
                            "message": "Тело запроса не прошло валидацию",
                            "details": [
                                {
                                    "type": "uuid_parsing",
                                    "loc": ["body", "applicationId"],
                                    "msg": "Input should be a valid UUID",
                                }
                            ],
                        },
                    },
                }
            }
        },
    },
}


@router.post(
    "/admin/clients",
    status_code=status.HTTP_201_CREATED,
    response_model=AccountCreatedResponse,
    summary="Создать аккаунт клиента",
    response_description="Идентификатор, тип и телефон владельца",
    responses=_ERRORS,
)
async def create_account(
    body: CreateAccountRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: CreateAccountUseCase = Depends(get_create_account_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AccountCreatedResponse:
    """Атомарно создаёт клинику/соло + владельца + роли + подписку (триал 30 дней).

    Пароль владельцу не выдаётся: он входит по коду подтверждения на свой номер
    (``POST /auth/otp/request`` → ``POST /auth/otp/verify``). Сообщение об этом
    уходит владельцу автоматически.

    **Заведение клиента из заявки.** Необязательный ``applicationId`` — заявка из
    очереди ``/admin/clinic-applications`` (лид с лендинга или заявка из
    приложения). По ней в той же транзакции, что и создание клиента:

    * в заявке проставляется ``convertedClinicId`` — созданная клиника (по этому
      признаку дашборд считает конверсию «заявки → клиенты»);
    * заявка становится ``reviewed``: рассмотревшим записывается вызвавший ручку
      сотрудник Skrezo, временем — «сейчас». Если заявка уже была ``reviewed``,
      рассмотревший и дата не меняются;
    * смена статуса пишется в аудит отдельной записью
      ``clinic_application.status_changed`` (``reason = converted``), а в
      ``clinic.created`` добавляется ``applicationId``.

    Заявка со статусом ``rejected`` тоже годится: команда вправе передумать.
    Телефон заявки с телефоном владельца **не сверяется** — человек мог оставить
    один номер, а владельцем стать другой.

    **Порядок проверок и повторы.** Заявку нельзя конвертировать дважды, но
    проверки идут по порядку: сначала телефон владельца, затем город и
    специализация, и только потом заявка. Поэтому:

    * повтор того же запроса (двойной клик, повтор после таймаута) после коммита
      первого отвечает ``409 phone_already_used`` — телефон тот же и проверяется
      раньше заявки; ``clinicId`` в таком ответе нет, клинику по заявке находят в
      ``convertedClinicId`` её карточки (``GET /admin/clinic-applications/{id}``);
    * ``409 application_already_converted`` с ``clinicId`` созданной ранее клиники
      приходит, когда телефон владельца другой либо когда запрос прошёл проверку
      телефона до коммита первого (два одновременных запроса: заявка берётся под
      блокировкой, второй ждёт первого);
    * в обоих случаях второй клиент не создаётся; несуществующий ``applicationId``
      — ``422 application_not_found``.
    """
    command = ProvisionCommand(
        type=body.type,
        admin_full_name=body.admin_full_name,
        admin_phone=body.admin_phone,
        city_id=body.city_id,
        clinic_name=body.name,
        address=body.address,
        business_phone=body.business_phone,
        seats_limit=body.seats,
        specialization_id=body.specialization_id,
        lang=body.language,
        application_id=body.application_id,
        actor_account_id=actor,
    )
    result = await use_case.execute(command)
    payload: dict[str, Any] = {"type": result.clinic.type.value}
    if body.application_id is not None:
        payload["applicationId"] = str(body.application_id)
    await audit.record(
        actor_id=actor,
        action="clinic.created",
        entity_type="clinic",
        entity_id=result.clinic.id,
        payload=payload,
    )
    # Статус заявки сменился (был pending/rejected) — тот же след в аудите, что
    # оставил бы PATCH /admin/clinic-applications/{id}/status, плюс причина.
    before = result.application_status_before
    if before is not None and before != ClinicApplicationStatus.REVIEWED:
        await audit.record(
            actor_id=actor,
            action="clinic_application.status_changed",
            entity_type="clinic_application",
            entity_id=body.application_id,
            payload={
                "status": ClinicApplicationStatus.REVIEWED.value,
                "reason": "converted",
                "clinicId": str(result.clinic.id),
            },
        )
    return AccountCreatedResponse(
        id=result.clinic.id,
        type=result.clinic.type,
        admin_phone=result.account.phone,
    )
