"""Тексты сообщения с кодом — по одному на канал.

Текст решает не канал, а домен: канал отвечает за доставку, иначе одно и то же
сообщение расходится по трём файлам и начинает жить своей жизнью.

Почему тексты разные:

* **SMS — латиницей.** Кириллица переводит сообщение в UCS-2, где сегмент это
  70 символов вместо 160 в GSM-7: то же сообщение стоит вдвое дороже. Для кода
  подтверждения читаемость от транслита не страдает. Последняя строка —
  ``@<домен кабинета> #<код>``: iPhone подставит код только на этом домене и в
  нашем приложении.
* **WhatsApp — обычным русским.** Посегментной тарификации нет, а сообщение
  читает человек.
* **Telegram Gateway** текста не принимает вовсе: он шлёт код своим шаблоном,
  поэтому передаётся только сам код.

Назначение кода (вход или смена пароля) видно в тексте: человек должен понимать,
что он подтверждает, — иначе «продиктуйте код из SMS» срабатывает слишком легко.
"""

from __future__ import annotations

from app.modules.identity.domain.models import OtpPurpose


def sms_text(
    code: str, purpose: OtpPurpose = OtpPurpose.LOGIN, *, bound_domain: str | None = None
) -> str:
    """Текст SMS (латиница — один сегмент GSM-7 вместо двух в кириллице).

    ``bound_domain`` — домен кабинета: последней строкой встаёт
    ``@office.skrezo.com #123456`` (формат Apple для кодов, привязанных к
    домену). iPhone предложит подставить код только на этом сайте и в
    приложении, к которому домен привязан, — фишинговая страница его не
    получит. Символы ``@`` и ``#`` есть в GSM-7, сообщение остаётся одним
    сегментом.
    """
    if purpose is OtpPurpose.PASSWORD_RESET:
        text = f"Kod dlya smeny parolya Skrezo: {code}. Nikomu ne soobshchayte etot kod."
    else:
        text = f"Kod Skrezo: {code}. Nikomu ne soobshchayte etot kod."
    if bound_domain:
        text += f"\n@{bound_domain} #{code}"
    return text


def whatsapp_text(code: str, purpose: OtpPurpose = OtpPurpose.LOGIN) -> str:
    """Текст WhatsApp-сообщения."""
    if purpose is OtpPurpose.PASSWORD_RESET:
        return f"Ваш код для смены пароля в Skrezo: {code}. Никому не сообщайте этот код."
    return f"Ваш код для входа в Skrezo: {code}. Никому не сообщайте этот код."
