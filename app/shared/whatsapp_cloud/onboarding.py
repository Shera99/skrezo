"""Подключение номера клиникой через Embedded Signup — вызовы Graph API.

Порядок Meta для Tech Provider после окна подключения в кабинете:

1. обменять одноразовый ``code`` (живёт 30 секунд) на бизнес-токен клиники —
   токен системного пользователя интеграции, без срока действия;
2. найти номер в её WABA: в режиме «номер остаётся в приложении WhatsApp
   Business» окно возвращает только ``waba_id``;
3. новый номер — зарегистрировать в Cloud API с PIN двухэтапной проверки;
   номер из приложения уже зарегистрирован — шаг пропускается;
4. подписать наше приложение на вебхуки WABA клиники;
5. номер из приложения — запросить синхронизацию контактов и истории: каждый
   запрос разрешён один раз и только в первые 24 часа после подключения.

Секрет приложения уходит только в обмен кода и никогда не попадает в лог.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

logger = structlog.get_logger(__name__)

#: Поля номера, которые подтверждены документацией Meta. Запрос несуществующего
#: поля Graph отклоняет целиком, поэтому список — только проверенный.
_PHONE_FIELDS = (
    "id,display_phone_number,verified_name,quality_rating,platform_type,"
    "code_verification_status,is_on_biz_app"
)

#: Типы синхронизации данных приложения WhatsApp Business (SMB App Data API).
SYNC_CONTACTS = "smb_app_state_sync"
SYNC_HISTORY = "history"


@dataclass(frozen=True, slots=True)
class PhoneNumberInfo:
    """Номер в WABA клиники так, как его описывает Graph API."""

    id: str
    display_phone: str  # формат Meta: «+996 700 123-456»
    verified_name: str | None
    quality_rating: str | None  # GREEN | YELLOW | RED | UNKNOWN | NA
    platform_type: str | None  # CLOUD_API | ON_PREMISE | NOT_APPLICABLE
    code_verification_status: str | None
    is_on_biz_app: bool  # номер одновременно работает в приложении WhatsApp Business

    @property
    def on_cloud_api(self) -> bool:
        return self.platform_type == "CLOUD_API"


class MetaOnboardingError(Exception):
    """Graph API отказал или не ответил на шаге подключения.

    ``status`` — HTTP-код (``None`` — сеть), ``meta_code`` — код ошибки Meta из
    тела ответа: по нему отличают «код истёк» от «нет доступа к WABA».
    """

    def __init__(
        self, message: str, *, status: int | None = None, meta_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.meta_code = meta_code


class MetaOnboardingClient(Protocol):
    """Шаги подключения номера, которые выполняются от имени клиники."""

    async def exchange_code(self, code: str) -> str:
        """Одноразовый код окна Meta → бизнес-токен клиники."""
        ...

    async def phone_numbers(self, waba_id: str, token: str) -> list[PhoneNumberInfo]:
        """Номера WABA клиники (доступ к WABA доказывает сам ответ)."""
        ...

    async def phone_number(self, phone_number_id: str, token: str) -> PhoneNumberInfo:
        """Текущее состояние номера: качество, платформа, приложение на номере."""
        ...

    async def register_number(self, phone_number_id: str, token: str, pin: str) -> None:
        """Включить новый номер в Cloud API (PIN двухэтапной проверки)."""
        ...

    async def subscribe_app(self, waba_id: str, token: str) -> None:
        """Подписать наше приложение на вебхуки WABA клиники."""
        ...

    async def unsubscribe_app(self, waba_id: str, token: str) -> None:
        """Отписать наше приложение от WABA клиники (отключение номера)."""
        ...

    async def request_sync(self, phone_number_id: str, token: str, sync_type: str) -> None:
        """Запросить контакты или историю приложения WhatsApp Business."""
        ...


def _phone_info(row: dict[str, Any]) -> PhoneNumberInfo:
    return PhoneNumberInfo(
        id=str(row["id"]),
        display_phone=str(row.get("display_phone_number") or ""),
        verified_name=row.get("verified_name"),
        quality_rating=row.get("quality_rating"),
        platform_type=row.get("platform_type"),
        code_verification_status=row.get("code_verification_status"),
        is_on_biz_app=bool(row.get("is_on_biz_app")),
    )


class GraphOnboardingClient:
    """Боевая реализация поверх httpx: общий пул соединений с транспортом."""

    def __init__(
        self, api_url: str, app_id: str, app_secret: str, client: httpx.AsyncClient
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._app_id = app_id
        self._app_secret = app_secret
        self._client = client

    async def _call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        step: str,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        try:
            resp = await self._client.request(
                method, f"{self._api_url}/{path}", params=params, json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            logger.warning("wa_onboarding_network_error", step=step, error=str(exc))
            raise MetaOnboardingError(f"Meta не ответила: {exc}") from exc
        try:
            data: dict[str, Any] = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            error = data.get("error") or {}
            # Тело ошибки — в лог без запроса: в параметрах обмена кода секрет.
            logger.error(
                "wa_onboarding_failed",
                step=step,
                status=resp.status_code,
                meta_code=error.get("code"),
                meta_message=str(error.get("message") or "")[:300],
            )
            raise MetaOnboardingError(
                str(error.get("message") or f"Graph API {resp.status_code}"),
                status=resp.status_code,
                meta_code=error.get("code"),
            )
        return data

    async def exchange_code(self, code: str) -> str:
        data = await self._call(
            "GET",
            "oauth/access_token",
            params={"client_id": self._app_id, "client_secret": self._app_secret, "code": code},
            step="exchange_code",
        )
        token = data.get("access_token")
        if not token:
            raise MetaOnboardingError("Meta не вернула токен")
        return str(token)

    async def phone_numbers(self, waba_id: str, token: str) -> list[PhoneNumberInfo]:
        data = await self._call(
            "GET",
            f"{waba_id}/phone_numbers",
            token=token,
            params={"fields": _PHONE_FIELDS},
            step="phone_numbers",
        )
        return [_phone_info(row) for row in data.get("data") or []]

    async def phone_number(self, phone_number_id: str, token: str) -> PhoneNumberInfo:
        data = await self._call(
            "GET",
            phone_number_id,
            token=token,
            params={"fields": _PHONE_FIELDS},
            step="phone_number",
        )
        return _phone_info(data)

    async def register_number(self, phone_number_id: str, token: str, pin: str) -> None:
        await self._call(
            "POST",
            f"{phone_number_id}/register",
            token=token,
            json={"messaging_product": "whatsapp", "pin": pin},
            step="register",
        )

    async def subscribe_app(self, waba_id: str, token: str) -> None:
        await self._call("POST", f"{waba_id}/subscribed_apps", token=token, step="subscribe")

    async def unsubscribe_app(self, waba_id: str, token: str) -> None:
        await self._call("DELETE", f"{waba_id}/subscribed_apps", token=token, step="unsubscribe")

    async def request_sync(self, phone_number_id: str, token: str, sync_type: str) -> None:
        await self._call(
            "POST",
            f"{phone_number_id}/smb_app_data",
            token=token,
            json={"messaging_product": "whatsapp", "sync_type": sync_type},
            step=f"sync_{sync_type}",
        )
