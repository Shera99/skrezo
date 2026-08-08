"""Тексты сообщения с кодом — по одному на канал.

Текст решает не канал, а домен: канал отвечает за доставку, иначе одно и то же
сообщение расходится по трём файлам и начинает жить своей жизнью.

Почему тексты разные:

* **SMS — латиницей.** Кириллица переводит сообщение в UCS-2, где сегмент это
  70 символов вместо 160 в GSM-7: то же сообщение стоит вдвое дороже. Для кода
  подтверждения читаемость от транслита не страдает.
* **WhatsApp — обычным русским.** Посегментной тарификации нет, а сообщение
  читает человек.
* **Telegram Gateway** текста не принимает вовсе: он шлёт код своим шаблоном,
  поэтому передаётся только сам код.

Назначение кода (вход или смена пароля) видно в тексте: человек должен понимать,
что он подтверждает, — иначе «продиктуйте код из SMS» срабатывает слишком легко.
"""

from __future__ import annotations

from app.modules.identity.domain.models import OtpPurpose


def sms_text(code: str, purpose: OtpPurpose = OtpPurpose.LOGIN) -> str:
    """Текст SMS (латиница — один сегмент GSM-7 вместо двух в кириллице)."""
    if purpose is OtpPurpose.PASSWORD_RESET:
        return f"Kod dlya smeny parolya Skrezo: {code}. Nikomu ne soobshchayte etot kod."
    return f"Kod Skrezo: {code}. Nikomu ne soobshchayte etot kod."


def whatsapp_text(code: str, purpose: OtpPurpose = OtpPurpose.LOGIN) -> str:
    """Текст WhatsApp-сообщения."""
    if purpose is OtpPurpose.PASSWORD_RESET:
        return f"Ваш код для смены пароля в Skrezo: {code}. Никому не сообщайте этот код."
    return f"Ваш код для входа в Skrezo: {code}. Никому не сообщайте этот код."
