"""Подключение номера клиники: причины «нужно переподключить» и параметры окна Meta."""

from __future__ import annotations

#: Почему номер перестал работать у нас — код в API, текст для людей.
RECONNECT_REASONS: dict[str, str] = {
    "partner_removed": "Доступ Skrezo к аккаунту WhatsApp Business отозван в Meta",
    "account_offboarded": (
        "Номер заново зарегистрирован в приложении WhatsApp Business на другом телефоне"
    ),
    "account_deleted": "Аккаунт WhatsApp Business удалён",
    "token_invalid": "Meta больше не принимает доступ клиники",
}

#: Окно Meta для номера, который остаётся в приложении WhatsApp Business.
SIGNUP_FEATURE_TYPE = "whatsapp_business_app_onboarding"
SIGNUP_SESSION_INFO_VERSION = "3"


def reconnect_message(reason: str | None) -> str | None:
    """Текст причины для экрана подключения; неизвестный код — общий текст."""
    if reason is None:
        return None
    return RECONNECT_REASONS.get(reason, "Номер перестал работать — подключите его заново")
