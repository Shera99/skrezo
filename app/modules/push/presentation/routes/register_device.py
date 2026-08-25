"""Роут: POST /clinic/devices — зарегистрировать устройство на пуш-уведомления."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import get_clinic_context
from app.modules.push.application.use_cases.register_device import RegisterDeviceUseCase
from app.modules.push.presentation.deps import get_register_device_use_case
from app.modules.push.presentation.schemas import DeviceResponse, RegisterDeviceRequest
from app.shared.auth import ClientContext

router = APIRouter()


@router.post(
    "/clinic/devices",
    response_model=DeviceResponse,
    summary="Зарегистрировать устройство на пуш-уведомления",
    response_description="Устройство после регистрации",
)
async def register_device(
    body: RegisterDeviceRequest,
    context: ClientContext = Depends(get_clinic_context),
    use_case: RegisterDeviceUseCase = Depends(get_register_device_use_case),
) -> DeviceResponse:
    """Завести токен устройства либо обновить уже известный.

    Вызывать при входе и при каждом открытии приложения — так ``lastSeenAt``
    остаётся свежим, а смена клиники или языка устройства подхватывается без
    отдельной ручки. ``token`` уникален на всю платформу: тот же токен с новыми
    ``accountId``/``clinicId`` перезаписывает владельца устройства, а не
    заводит дубль (переезд на другой аккаунт/клинику того же телефона).

    Доступна любому аутентифицированному сотруднику с выбранной клиникой —
    отдельного права не требует, устройство и без того принадлежит только ему.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    device = await use_case.execute(
        account_id=context.account_id,
        clinic_id=context.clinic_id,
        token=body.token,
        platform=body.platform,
        app_version=body.app_version,
        locale=body.locale,
    )
    return DeviceResponse.from_device(device)
