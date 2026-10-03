"""Use case: клиника подключает свой номер сама — окно Meta в кабинете (Embedded Signup).

Кабинет открывает окно Meta (Facebook Login for Business). Владелец входит в
свой бизнес-аккаунт и выбирает номер, Meta отдаёт кабинету одноразовый код и
``waba_id``. Код живёт 30 секунд, поэтому кабинет присылает его сюда сразу, а
всё остальное делает бэкенд: обмен кода на токен клиники, поиск номера в её
WABA, регистрацию нового номера, подписку нашего приложения на вебхуки WABA,
сохранение привязки. Затем по возможности — запрос контактов и истории
приложения WhatsApp Business и синхронизация шаблонов.

Сбой до сохранения ничего не меняет: окно можно пройти ещё раз с новым кодом.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, BadRequestError, ConflictError, UpstreamError
from app.modules.whatsapp.application.services.app_data_sync import request_app_data
from app.modules.whatsapp.application.services.credentials import (
    SELF_SERVICE,
    e164,
    new_two_step_pin,
)
from app.modules.whatsapp.application.use_cases.connection_status import signup_config
from app.modules.whatsapp.application.use_cases.sync_templates import SyncTemplatesUseCase
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
    reset_self_service,
)
from app.shared.crypto import secret_box
from app.shared.db.enums import WhatsAppMode
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.whatsapp_cloud import (
    MetaOnboardingClient,
    MetaOnboardingError,
    PhoneNumberInfo,
    WhatsAppCloudClient,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EmbeddedSignupInput:
    code: str
    waba_id: str
    #: Окно с новым номером возвращает его id; окно «номер в приложении» — нет.
    phone_number_id: str | None = None


class ConnectViaEmbeddedSignupUseCase:
    def __init__(
        self,
        session: AsyncSession,
        repository: WhatsAppRepository,
        onboarding: MetaOnboardingClient | None,
        wa_client: WhatsAppCloudClient,
    ) -> None:
        self._session = session
        self._repository = repository
        self._onboarding = onboarding
        self._wa = wa_client

    async def execute(
        self, clinic_id: uuid.UUID, actor_id: uuid.UUID, data: EmbeddedSignupInput
    ) -> ClinicWhatsApp:
        onboarding = self._onboarding
        if onboarding is None or not signup_config().available:
            raise AppError(
                "Подключение номера из кабинета пока не настроено — обратитесь в Skrezo",
                code="whatsapp_signup_unavailable",
                status_code=503,
            )
        token = await self._exchange(onboarding, data.code)
        number = self._pick(await self._numbers(onboarding, data.waba_id, token), data)

        current = await self._repository.binding_by_clinic(clinic_id)
        if current is not None and current.active and current.phone_number_id != number.id:
            raise ConflictError(
                "К клинике уже подключён другой номер — сначала отключите его",
                code="whatsapp_already_connected",
            )
        taken = await self._repository.binding_by_phone_number_id(number.id, include_inactive=True)
        if taken is not None and taken.clinic_id != clinic_id:
            raise ConflictError(
                "Этот номер уже подключён к другой клинике", code="phone_number_taken"
            )

        box = secret_box()
        # PIN нужен только новому номеру; переподключение того же номера его сохраняет.
        pin_enc = (
            current.two_step_pin_enc
            if current is not None and current.phone_number_id == number.id
            else None
        )
        if not number.is_on_biz_app and not number.on_cloud_api:
            pin = new_two_step_pin()
            await self._step(onboarding.register_number(number.id, token, pin), "register")
            pin_enc = box.encrypt(pin)
        await self._step(onboarding.subscribe_app(data.waba_id, token), "subscribe")

        now = datetime.now(UTC)
        binding = await self._repository.upsert_binding(
            clinic_id,
            phone_number_id=number.id,
            display_phone=e164(number.display_phone),
            waba_id=data.waba_id,
            mode=WhatsAppMode.COEXISTENCE if number.is_on_biz_app else WhatsAppMode.API_ONLY,
            active=True,
        )
        # Новое подключение — новое окно синхронизации и чистое состояние.
        reset_self_service(binding)
        binding.connected_via = SELF_SERVICE
        binding.access_token_enc = box.encrypt(token)
        binding.two_step_pin_enc = pin_enc
        binding.connected_by = actor_id
        binding.connected_at = now
        binding.verified_name = number.verified_name
        binding.quality_rating = number.quality_rating
        binding.status_checked_at = now
        await self._session.commit()

        # Дальше — по возможности: номер уже работает, остальное догонит проверка.
        if await request_app_data(binding, onboarding, now):
            await self._session.commit()
        await self._sync_templates(clinic_id)
        logger.info(
            "wa_self_service_connected",
            clinic_id=str(clinic_id),
            mode=binding.mode.value,
            registered=pin_enc is not None and not number.on_cloud_api,
        )
        return binding

    @staticmethod
    async def _exchange(onboarding: MetaOnboardingClient, code: str) -> str:
        try:
            return await onboarding.exchange_code(code)
        except MetaOnboardingError as exc:
            if exc.status is not None and exc.status < 500:
                raise BadRequestError(
                    "Код подключения недействителен или истёк — пройдите подключение ещё раз",
                    code="signup_code_invalid",
                ) from exc
            raise UpstreamError(
                "Meta не ответила — попробуйте подключить номер ещё раз", code="meta_unavailable"
            ) from exc

    @staticmethod
    async def _numbers(
        onboarding: MetaOnboardingClient, waba_id: str, token: str
    ) -> list[PhoneNumberInfo]:
        try:
            return await onboarding.phone_numbers(waba_id, token)
        except MetaOnboardingError as exc:
            if exc.status is not None and exc.status < 500:
                raise BadRequestError(
                    "Нет доступа к выбранному аккаунту WhatsApp Business",
                    code="waba_access_denied",
                ) from exc
            raise UpstreamError(
                "Meta не ответила — попробуйте подключить номер ещё раз", code="meta_unavailable"
            ) from exc

    @staticmethod
    def _pick(numbers: list[PhoneNumberInfo], data: EmbeddedSignupInput) -> PhoneNumberInfo:
        if data.phone_number_id:
            for number in numbers:
                if number.id == data.phone_number_id:
                    return number
            raise BadRequestError(
                "Номер не найден в выбранном аккаунте WhatsApp Business",
                code="phone_number_not_in_waba",
            )
        if len(numbers) == 1:
            return numbers[0]
        if not numbers:
            raise BadRequestError(
                "В выбранном аккаунте WhatsApp Business нет номеров", code="waba_has_no_numbers"
            )
        raise BadRequestError(
            "В аккаунте несколько номеров — укажите, какой подключить",
            code="phone_number_required",
            extra={
                "phoneNumbers": [
                    {"id": n.id, "displayPhone": n.display_phone, "verifiedName": n.verified_name}
                    for n in numbers
                ]
            },
        )

    @staticmethod
    async def _step(call: Awaitable[None], step: str) -> None:
        try:
            await call
        except MetaOnboardingError as exc:
            raise UpstreamError(
                "Meta не завершила подключение номера — попробуйте ещё раз",
                code="meta_step_failed",
                extra={"step": step},
            ) from exc

    async def _sync_templates(self, clinic_id: uuid.UUID) -> None:
        try:
            await SyncTemplatesUseCase(self._session, self._repository, self._wa).execute(clinic_id)
        except Exception as exc:
            # Шаблоны можно синхронизировать и кнопкой; подключение уже состоялось
            # и сохранено — сбой этого шага не должен превращаться в ошибку ответа.
            await self._session.rollback()
            reason = exc.code if isinstance(exc, AppError) else type(exc).__name__
            logger.warning("wa_self_service_templates_skipped", reason=reason)
