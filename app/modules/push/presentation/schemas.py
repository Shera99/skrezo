"""Схемы модуля уведомлений: устройства, настройки, инбокс."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from pydantic import Field

from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import DEFAULT_LANG, DevicePlatform, EventPriority, Lang
from app.shared.db.models.device import Device
from app.shared.db.models.inbox_item import InboxItem
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


class InboxItemOut(CamelModel):
    """Запись инбокса — источник истины «что произошло и прочитано ли»."""

    id: int
    type: str = Field(description="Тип события каталога", examples=["appointment.created"])
    priority: EventPriority
    title: str
    body: str
    entity_type: str | None = Field(
        default=None, description="Куда ведёт запись (`appointment`, `payment`…)"
    )
    entity_id: uuid.UUID | None = None
    read_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_orm_item(cls, i: InboxItem) -> InboxItemOut:
        return cls(
            id=i.id,
            type=i.type,
            priority=i.priority,
            title=i.title,
            body=i.body,
            entity_type=i.entity_type,
            entity_id=i.entity_id,
            read_at=i.read_at,
            created_at=i.created_at,
        )


class InboxResponse(CamelModel):
    """Страница инбокса + счётчик непрочитанного (= бейдж приложения)."""

    items: list[InboxItemOut] = Field(
        description="Новые сверху; продолжение — `beforeId` = id последнего"
    )
    unread_count: int = Field(description="Непрочитанных всего (для бейджа)", examples=[3])


class InboxReadResponse(CamelModel):
    """Результат отметки прочитанности."""

    unread_count: int = Field(description="Непрочитанных после операции")


class InboxDisplayedRequest(CamelModel):
    """Подтверждение ПОКАЗА записей — приложение шлёт с переднего плана.

    Именно показ, а не «байты дошли»: сокет мог доставить сигнал свёрнутому
    приложению, и человек ничего не увидел — тогда отложенный пуш обязан уйти.
    Достаточно любого одного устройства адресата.
    """

    ids: list[int] = Field(
        min_length=1,
        max_length=200,
        description="id записей инбокса, отрисованных на экране",
        examples=[[101, 102]],
    )


class InboxDisplayedResponse(CamelModel):
    """Результат подтверждения показа."""

    canceled: int = Field(
        description="Сколько отложенных пушей отменено (уже отправленные не трогаются)"
    )


class TelemetryRowOut(CamelModel):
    """Доставляемость по паре (платформа, провайдер)."""

    platform: str | None = Field(default=None, examples=["android"])
    provider: str | None = Field(default=None, examples=["fcm"])
    sent: int = Field(description="Отправлено успешно")
    failed: int = Field(description="Ошибки провайдера (включая мёртвые токены)")
    displayed: int = Field(description="Показ подтверждён приложением")
    display_rate: float = Field(
        description="displayed / sent, 0..1 (0 при отсутствии отправок)", examples=[0.87]
    )


class NotificationTelemetryResponse(CamelModel):
    """Сводка доставляемости пушей за период (для команды Skrezo)."""

    days: int
    rows: list[TelemetryRowOut]
    total_sent: int
    total_failed: int
    total_displayed: int
