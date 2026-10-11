"""Оформление карточек для чата команды Skrezo (Telegram, HTML-разметка).

Карточка — сообщение о заявке или клиенте: кто это, откуда и как с ним
связаться. Все карточки собираются из одних кирпичиков, чтобы в канале они
выглядели одинаково: строка «иконка + жирная подпись + значение», время по
Бишкеку, кнопки «написать» под сообщением.

⚠️ Всё, что пришло от людей (имя, название клиники, комментарий), проходит через
:func:`esc` или :func:`field`: ``<`` или ``&`` в названии иначе сломали бы
разметку, и Bot API отклонил бы сообщение целиком. Транспорт на такой случай
повторит отправку простым текстом, но это страховка, а не норма.
"""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from app.shared.notifications.channels import ChatButton

# Команда Skrezo в Бишкеке: время в карточках — по нему, а не по UTC и не по
# поясу клиники (у каждой свой, а читают все карточки одни и те же люди).
TEAM_TIMEZONE = ZoneInfo("Asia/Bishkek")

# Потолок комментария в карточке: у Telegram лимит 4096 символов на сообщение, а
# комментарий с формы — единственное длинное поле карточки.
_QUOTE_LIMIT = 1000


def esc(value: object) -> str:
    """Значение для HTML-текста Bot API: ``<``, ``>``, ``&``, ``"`` — сущностями."""
    return html.escape(str(value))


def field(icon: str, label: str, value: object) -> str:
    """Строка карточки «📞 <b>Телефон:</b> +996…»; значение экранируется."""
    return f"{icon} <b>{label}:</b> {esc(value)}"


def quote(text: str) -> str:
    """Свободный текст (комментарий с формы) цитатой; слишком длинный — обрезается."""
    if len(text) > _QUOTE_LIMIT:
        text = text[:_QUOTE_LIMIT].rstrip() + "…"
    return f"<blockquote>{esc(text)}</blockquote>"


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 приём», «3 приёма», «12 приёмов» — иначе карточка читается машинно."""
    if count % 10 == 1 and count % 100 != 11:
        word = one
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        word = few
    else:
        word = many
    return f"{count} {word}"


def team_date(moment: datetime) -> str:
    """Дата по Бишкеку: «11.10.2026»."""
    return moment.astimezone(TEAM_TIMEZONE).strftime("%d.%m.%Y")


def team_datetime(moment: datetime) -> str:
    """Дата и время по Бишкеку: «11.10.2026 14:32»."""
    return moment.astimezone(TEAM_TIMEZONE).strftime("%d.%m.%Y %H:%M")


def contact_buttons(phone: str) -> tuple[ChatButton, ...]:
    """Кнопки «написать» на номер в E.164: WhatsApp и Telegram.

    Кнопки «позвонить» нет: Bot API пускает в кнопки только ``http(s)://`` и
    ``tg://``. Номер в тексте карточки стоит простым текстом (без ``<code>``) —
    так Telegram сам делает его телефонной ссылкой.

    Ссылка Telegram по номеру откроет чат, только если человек разрешил искать
    себя по номеру; иначе Telegram ответит «пользователь не найден» — WhatsApp
    поэтому первым.
    """
    digits = phone.removeprefix("+")
    if not digits.isdigit():
        return ()
    return (
        ("💬 WhatsApp", f"https://wa.me/{digits}"),
        ("✈️ Telegram", f"https://t.me/+{digits}"),
    )
