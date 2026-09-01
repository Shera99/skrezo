"""Сообщение пуша — транспортно-независимое представление одного уведомления.

Строится диспетчером (:mod:`app.modules.push.application.dispatcher`) и отдаётся
каналу (FCM/APNs/лог), который сам раскладывает его в форму своей платформы.
Общая форма payload описана в ``docs/PUSH_NOTIFICATIONS.md`` §3.4 и каталоге
пушей ``PUSH_CATALOG.md`` §1.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PushMessage:
    """Одно уведомление до раскладки под конкретную платформу.

    ``data`` уходит всегда — в том числе у тихого ``sync.day``, у которого нет
    видимого текста. Значения строковые: FCM принимает ``data`` только строками,
    и держать один тип на обе платформы проще, чем ветвить по каналам.
    """

    #: Полезная нагрузка ``data`` (``type``, ``id``, ``clinicId``, ``appointmentId``…).
    data: dict[str, str]
    #: Заголовок; ``None`` — тихий data-only пуш (``sync.day``): notification-блока нет.
    title: str | None = None
    #: Текст под заголовком.
    body: str | None = None
    #: Схлопывание (FCM ``collapse_key`` / APNs ``apns-collapse-id``).
    collapse_key: str | None = None
    #: Срочность: high-priority (FCM) / time-sensitive (APNs).
    urgent: bool = False
    #: Канал Android — версионированный id (``.v1``): настройки канала после
    #: создания неизменяемы, немодифицируемый id — единственный путь миграции.
    android_channel: str = "appointments-digest.v1"
    #: Категория iOS — для кнопок действий на экране блокировки.
    ios_category: str | None = None
    #: Срок годности (сек): телефон офлайн дольше — платформа выбрасывает пуш,
    #: не доставив. «Приём через 15 минут», доставленный через сорок, хуже
    #: недоставленного. ``None`` — по умолчанию платформы.
    ttl_seconds: int | None = None
    #: Число непрочитанных адресата — в каждый пуш, чтобы бейдж на устройстве
    #: самовосстанавливался после любых пропусков (ТЗ-СУ §7).
    badge: int | None = None
    #: Визуальная группировка (APNs ``thread-id``): стопка вместо пяти строк.
    thread_id: str | None = None

    @property
    def is_silent(self) -> bool:
        """Тихий пуш без видимого текста — только ``data`` (``content-available``)."""
        return self.title is None
