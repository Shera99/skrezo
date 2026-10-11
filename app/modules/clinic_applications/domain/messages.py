"""Карточка новой заявки клиники для чата команды Skrezo.

Адресат — общий канал в Telegram, а не клиент, поэтому текст многострочный,
размечен HTML и без оглядки на длину сегмента: чат бесплатный. Под карточкой —
кнопки «написать в WhatsApp / Telegram» на номер заявителя.

Обе «двери» очереди дают одну карточку с разной шапкой: заявка с сайта
(``phone_verified = false``, известны имя и номер) и заявка из приложения
(телефон подтверждён кодом, есть клиника, город и размер). Пустые поля в
карточку не попадают — иначе в канале были бы строки-прочерки.

Всё введённое человеком экранируется (:mod:`app.shared.notifications.team_format`):
``<`` или ``&`` в названии клиники не должны стоить заявке доставки.
"""

from __future__ import annotations

from app.modules.clinic_applications.domain.models import ApplicationRow
from app.shared.notifications.channels import ChatButton
from app.shared.notifications.team_format import (
    contact_buttons,
    esc,
    field,
    quote,
    team_datetime,
)


def new_application_text(application: ApplicationRow) -> str:
    """Карточка заявки для чата команды (HTML).

    Подтверждённость телефона стоит в строке с номером: по неподтверждённому
    номеру нельзя отправлять доступы, и менеджер должен видеть это до звонка.
    """
    from_site = not application.phone_verified
    lines = [
        "🦷 <b>Новая заявка с сайта</b>" if from_site else "📱 <b>Новая заявка из приложения</b>",
        "",
    ]
    if application.full_name:
        lines.append(field("👤", "Контакт", application.full_name))
    phone_mark = "⚠️ не подтверждён" if from_site else "✅ подтверждён кодом"
    lines.append(f"📞 <b>Телефон:</b> {esc(application.phone)} · {phone_mark}")
    if application.company_name:
        lines.append(field("🏥", "Клиника", application.company_name))
    if application.city_name:
        lines.append(field("📍", "Город", application.city_name))
    if application.doctors_count:
        lines.append(field("👨‍⚕️", "Врачей", application.doctors_count.value))
    if application.source:
        lines.append(field("🔗", "Источник", application.source))
    if application.comment:
        lines.extend(["", "💬 <b>Комментарий:</b>", quote(application.comment)])
    lines.extend(
        [
            "",
            f"🕒 {team_datetime(application.created_at)}",
            "#заявка #сайт" if from_site else "#заявка #приложение",
        ]
    )
    return "\n".join(lines)


def new_application_buttons(application: ApplicationRow) -> tuple[ChatButton, ...]:
    """Кнопки под карточкой заявки: написать заявителю."""
    return contact_buttons(application.phone)
