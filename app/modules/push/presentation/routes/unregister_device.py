"""Роут: DELETE /clinic/devices/{token} — снять устройство с пуш-уведомлений."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.access.presentation.deps import get_clinic_context
from app.modules.push.application.use_cases.unregister_device import UnregisterDeviceUseCase
from app.modules.push.presentation.deps import get_unregister_device_use_case
from app.shared.api.schemas import OkResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.delete(
    "/clinic/devices/{token}",
    response_model=OkResponse,
    summary="Снять устройство с пуш-уведомлений",
    response_description="Подтверждение снятия",
)
async def unregister_device(
    token: str,
    context: ClientContext = Depends(get_clinic_context),
    use_case: UnregisterDeviceUseCase = Depends(get_unregister_device_use_case),
) -> OkResponse:
    """Снять токен устройства — при выходе из приложения (logout).

    Идемпотентна: если токена уже нет или он принадлежит другому аккаунту,
    ответ всё равно `200 OK` — на выходе из приложения важно перестать
    получать пуши, а не разбирать код ошибки. Снимается устройство целиком,
    вне зависимости от клиники, на которую оно было зарегистрировано последней
    (владение проверяется только по аккаунту из токена).
    """
    await use_case.execute(token, account_id=context.account_id)
    return OkResponse()
