"""Схемы модуля пушей: устройства и настройки уведомлений."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from pydantic import Field

from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import DEFAULT_LANG, DevicePlatform, Lang
from app.shared.db.models.device import Device
from app.shared.db.models.notification_pref import NotificationPref

_DISABLED_DESCRIPTION = (
    "Отключённые типы пушей — значения `type` из каталога "
    "(app/modules/push/domain/catalog.py), например `day.morning`. Группу "
    "`subscription.*` отключить нельзя: фильтр применяется на отправке, а не здесь"
)


class RegisterDeviceRequest(CamelModel):
    """Регистрация токена устройства на пуши."""

    token: str = Field(min_length=1, description="Токен FCM (Android) или device token APNs (iOS)")
    platform: DevicePlatform = Field(description="Платформа устройства", examples=["android"])
    app_version: str | None = Field(
        default=None, description="Версия приложения, если известна", examples=["1.4.0"]
    )
    locale: Lang = Field(
        default=DEFAULT_LANG, description="Язык текста пуша на этом устройстве", examples=["ru"]
    )


class DeviceResponse(CamelModel):
    """Устройство, зарегистрированное на пуши."""

    id: uuid.UUID
    token: str
    platform: DevicePlatform
    app_version: str | None = None
    locale: Lang
    created_at: datetime
    last_seen_at: datetime = Field(description="Когда устройство регистрировалось в последний раз")

    @classmethod
    def from_device(cls, d: Device) -> DeviceResponse:
        return cls(
            id=d.id,
            token=d.token,
            platform=d.platform,
            app_version=d.app_version,
            locale=d.locale,
            created_at=d.created_at,
            last_seen_at=d.last_seen_at,
        )


class UpdatePrefsRequest(NotNullFieldsModel):
    """Настройки уведомлений: передаются только изменяемые поля.

    ``null`` у `quietFrom`/`quietTo` осмыслен — снимает тихие часы. Для
    `disabled` (NOT NULL в БД) явный ``null`` отвергается на валидации (422):
    «ничего не отключено» — это пустой список ``[]``, а не отсутствие поля.
    """

    NOT_NULL_FIELDS = ("disabled",)

    disabled: list[str] | None = Field(
        default=None, description=_DISABLED_DESCRIPTION, examples=[["day.morning"]]
    )
    quiet_from: time | None = Field(
        default=None,
        description="Начало тихих часов по времени клиники; `null` — снять",
        examples=["22:00:00"],
    )
    quiet_to: time | None = Field(
        default=None,
        description="Конец тихих часов по времени клиники; `null` — снять",
        examples=["08:00:00"],
    )


class PrefsResponse(CamelModel):
    """Действующие настройки уведомлений своего членства (аккаунт + клиника)."""

    disabled: list[str] = Field(description=_DISABLED_DESCRIPTION)
    quiet_from: time | None = Field(default=None, description="Начало тихих часов")
    quiet_to: time | None = Field(default=None, description="Конец тихих часов")

    @classmethod
    def from_pref(cls, p: NotificationPref) -> PrefsResponse:
        return cls(disabled=list(p.disabled), quiet_from=p.quiet_from, quiet_to=p.quiet_to)
