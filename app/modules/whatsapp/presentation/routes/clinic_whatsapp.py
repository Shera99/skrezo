"""Роуты: номер WhatsApp клиники — состояние, подключение через окно Meta, отключение.

Клиника подключает свой номер сама (Embedded Signup): кабинет открывает окно
Meta, получает одноразовый код и ``waba_id`` и сразу отправляет их сюда — код
живёт 30 секунд. Менять подключение может право ``whatsapp.manage`` (владелец
всегда), смотреть состояние — и администратор переписки.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_any_permission, require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.whatsapp.application.use_cases.connection_status import (
    ConnectionStatus,
    GetConnectionStatusUseCase,
    signup_config,
)
from app.modules.whatsapp.application.use_cases.disconnect_whatsapp import (
    DisconnectWhatsAppUseCase,
)
from app.modules.whatsapp.application.use_cases.embedded_signup import (
    ConnectViaEmbeddedSignupUseCase,
    EmbeddedSignupInput,
)
from app.modules.whatsapp.application.use_cases.refresh_connection import (
    RefreshConnectionUseCase,
)
from app.modules.whatsapp.presentation.deps import (
    get_connection_status_use_case,
    get_disconnect_whatsapp_use_case,
    get_embedded_signup_use_case,
    get_refresh_connection_use_case,
)
from app.modules.whatsapp.presentation.schemas import (
    EmbeddedSignupRequest,
    WhatsAppConnectionResponse,
)
from app.shared.auth import ClientContext

router = APIRouter()


def _err(code: str, message: str, description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"example": {"error": code, "message": message}}},
    }


_NOT_CONNECTED = _err(
    "whatsapp_not_connected", "WhatsApp не подключён к клинике", "Номер не подключён"
)


@router.get(
    "/clinic/whatsapp",
    response_model=WhatsAppConnectionResponse,
    summary="Номер WhatsApp клиники и параметры окна подключения",
    response_description="Подключённый номер (или `connected: false`) и настройки окна Meta",
)
async def get_whatsapp_connection(
    context: ClientContext = Depends(
        require_any_permission("whatsapp.manage", "conversations.manage")
    ),
    use_case: GetConnectionStatusUseCase = Depends(get_connection_status_use_case),
) -> WhatsAppConnectionResponse:
    """Экран «WhatsApp» в настройках кабинета.

    `connected: false` и `signup.available: true` — показать кнопку «Подключить
    WhatsApp»: `FB.init` с `signup.appId` и `signup.graphApiVersion`, затем
    `FB.login` с `config_id = signup.configId`, `response_type: "code"`,
    `override_default_response_type: true` и `extras: {setup: {}, featureType:
    signup.featureType, sessionInfoVersion: signup.sessionInfoVersion}`.
    `needsReconnect` заполнен — номер перестал работать, нужно пройти окно заново.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    return WhatsAppConnectionResponse.from_status(await use_case.execute(context.clinic_id))


@router.post(
    "/clinic/whatsapp/embedded-signup",
    response_model=WhatsAppConnectionResponse,
    summary="Подключить номер через окно Meta (Embedded Signup)",
    response_description="Подключённый номер",
    responses={
        400: _err(
            "signup_code_invalid",
            "Код подключения недействителен или истёк — пройдите подключение ещё раз",
            "Код истёк, нет доступа к WABA (`waba_access_denied`), номер не найден "
            "(`phone_number_not_in_waba`), в WABA несколько номеров (`phone_number_required`, "
            "в теле — `phoneNumbers`) или номеров нет (`waba_has_no_numbers`)",
        ),
        409: _err(
            "whatsapp_already_connected",
            "К клинике уже подключён другой номер — сначала отключите его",
            "Подключён другой номер либо этот номер у другой клиники (`phone_number_taken`)",
        ),
        502: _err(
            "meta_unavailable",
            "Meta не ответила — попробуйте подключить номер ещё раз",
            "Meta не ответила или не завершила шаг (`meta_step_failed`, в теле — `step`)",
        ),
        503: _err(
            "whatsapp_signup_unavailable",
            "Подключение номера из кабинета пока не настроено — обратитесь в Skrezo",
            "Приложение Meta или ключ шифрования не настроены на сервере",
        ),
    },
)
async def connect_via_embedded_signup(
    body: EmbeddedSignupRequest,
    context: ClientContext = Depends(require_permission("whatsapp.manage")),
    use_case: ConnectViaEmbeddedSignupUseCase = Depends(get_embedded_signup_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> WhatsAppConnectionResponse:
    """Завершить подключение после окна Meta.

    Кабинет шлёт сюда `code` из колбэка `FB.login` и `wabaId` из события
    `WA_EMBEDDED_SIGNUP` сразу, не дожидаясь действий пользователя: код живёт 30
    секунд. Бэкенд меняет код на токен клиники (хранится только
    зашифрованным), находит номер, подписывает Skrezo на вебхуки аккаунта и
    запрашивает контакты и историю переписки приложения WhatsApp Business.
    Повторное подключение того же номера (после отзыва доступа) разрешено.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    binding = await use_case.execute(
        context.clinic_id,
        context.account_id,
        EmbeddedSignupInput(
            code=body.code, waba_id=body.waba_id, phone_number_id=body.phone_number_id
        ),
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="whatsapp.connected",
        entity_type="clinic",
        entity_id=context.clinic_id,
        payload={
            "phoneNumberId": binding.phone_number_id,
            "displayPhone": binding.display_phone,
            "mode": binding.mode.value,
        },
    )
    return WhatsAppConnectionResponse.from_status(ConnectionStatus(binding, signup_config()))


@router.post(
    "/clinic/whatsapp/refresh",
    response_model=WhatsAppConnectionResponse,
    summary="Обновить состояние номера у Meta",
    response_description="Номер с актуальными именем, качеством и доступом",
    responses={
        404: _NOT_CONNECTED,
        502: _err(
            "meta_unavailable", "Meta не ответила — попробуйте обновить позже", "Meta не ответила"
        ),
    },
)
async def refresh_whatsapp_connection(
    context: ClientContext = Depends(require_permission("whatsapp.manage")),
    use_case: RefreshConnectionUseCase = Depends(get_refresh_connection_use_case),
) -> WhatsAppConnectionResponse:
    """Перечитать имя, качество и доступ к номеру; успешный ответ снимает
    отметку «Meta не принимает доступ клиники», если она стояла."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    binding = await use_case.execute(context.clinic_id)
    return WhatsAppConnectionResponse.from_status(ConnectionStatus(binding, signup_config()))


@router.delete(
    "/clinic/whatsapp",
    response_model=WhatsAppConnectionResponse,
    summary="Отключить номер WhatsApp от Skrezo",
    response_description="`connected: false`; переписка и архив сохраняются",
    responses={404: _NOT_CONNECTED},
)
async def disconnect_whatsapp(
    context: ClientContext = Depends(require_permission("whatsapp.manage")),
    use_case: DisconnectWhatsAppUseCase = Depends(get_disconnect_whatsapp_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> WhatsAppConnectionResponse:
    """Отписать Skrezo от аккаунта клиники и забыть её токен.

    Переписка, архив и контакты остаются; приложение WhatsApp Business на
    телефоне продолжает работать. Подключить номер снова можно через окно Meta.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    binding = await use_case.execute(context.clinic_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="whatsapp.disconnected",
        entity_type="clinic",
        entity_id=context.clinic_id,
        payload={"phoneNumberId": binding.phone_number_id},
    )
    return WhatsAppConnectionResponse.from_status(ConnectionStatus(binding, signup_config()))
