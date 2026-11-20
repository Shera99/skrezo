"""Подключение номера клиники: причины «нужно переподключить» и параметры окна Meta."""

from __future__ import annotations

from typing import Final, Literal

#: Почему номер перестал работать у нас — код в API, текст для людей.
RECONNECT_REASONS: dict[str, str] = {
    "partner_removed": "Доступ Skrezo к аккаунту WhatsApp Business отозван в Meta",
    "account_offboarded": (
        "Номер заново зарегистрирован в приложении WhatsApp Business на другом телефоне"
    ),
    "account_deleted": "Аккаунт WhatsApp Business удалён",
    "token_invalid": "Meta больше не принимает доступ клиники",
}

#: Пути подключения номера (``clinic_whatsapp.connected_via``): вручную командой
#: Skrezo (номер в WABA Skrezo, общий токен) или самой клиникой через окно Meta
#: (номер в WABA клиники, её бизнес-токен).
MANUAL = "manual"
SELF_SERVICE = "embedded_signup"

#: Состояние подключения для списка команды Skrezo: номер работает; номер
#: включён, но Meta его больше не обслуживает у нас (нужно переподключить);
#: клиника или команда отключила номер (строка и история сохранены).
ConnectionState = Literal["working", "needs_reconnect", "disconnected"]
WORKING: Final = "working"
NEEDS_RECONNECT: Final = "needs_reconnect"
DISCONNECTED: Final = "disconnected"


def connection_state(*, active: bool, needs_reconnect: bool) -> ConnectionState:
    """Одно из ``working`` / ``needs_reconnect`` / ``disconnected``.

    Отключённый номер — ``disconnected`` независимо от отметки о переподключении:
    он и так не принимает сообщения.
    """
    if not active:
        return DISCONNECTED
    return NEEDS_RECONNECT if needs_reconnect else WORKING


#: Окно Meta для номера, который остаётся в приложении WhatsApp Business.
SIGNUP_FEATURE_TYPE = "whatsapp_business_app_onboarding"
SIGNUP_SESSION_INFO_VERSION = "3"


def reconnect_message(reason: str | None) -> str | None:
    """Текст причины для экрана подключения; неизвестный код — общий текст."""
    if reason is None:
        return None
    return RECONNECT_REASONS.get(reason, "Номер перестал работать — подключите его заново")
