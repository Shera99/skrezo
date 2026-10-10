"""Текст уведомления команды Skrezo о новой заявке с лендинга.

Адресат — общий чат в Telegram, а не клиент, поэтому текст многострочный и без
оглядки на длину сегмента: чат бесплатный.

Разметку (HTML/Markdown) намеренно не используем — в названии клиники и в
комментарии бывают ``<``, ``&`` и ``_``, и Bot API отклонил бы такое сообщение
целиком, то есть заявка осталась бы незамеченной из-за символа в тексте.
"""

from __future__ import annotations

from app.shared.db.models.clinic_application import ClinicApplication


def new_lead_text(application: ClinicApplication) -> str:
    """Карточка заявки с лендинга для чата команды.

    Телефон помечен как неподтверждённый — по такому номеру нельзя отправлять
    доступы, и менеджер должен знать это до звонка. Необязательные поля в текст
    не попадают, пока пусты: иначе в чате были бы строки-прочерки.
    """
    lines = [
        "🦷 Новая заявка с лендинга",
        f"Контакт: {application.full_name}, {application.phone} (телефон не подтверждён)",
    ]
    if application.company_name:
        lines.append(f"Клиника: {application.company_name}")
    if application.doctors_count:
        lines.append(f"Врачей: {application.doctors_count.value}")
    if application.source:
        lines.append(f"Источник: {application.source}")
    if application.comment:
        lines.append(f"Комментарий: {application.comment}")
    return "\n".join(lines)
