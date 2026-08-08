"""Текст уведомления команды Skrezo о новом лиде.

Адресат — общий чат в Telegram, а не клиент, поэтому текст многострочный и без
оглядки на длину сегмента: чат бесплатный.

Разметку (HTML/Markdown) намеренно не используем — в названии клиники и в
комментарии бывают ``<``, ``&`` и ``_``, и Bot API отклонил бы такое сообщение
целиком, то есть лид остался бы незамеченным из-за символа в тексте.
"""

from __future__ import annotations

from app.shared.db.models.lead import Lead


def new_lead_text(lead: Lead) -> str:
    """Карточка лида для чата команды."""
    lines = [
        "🦷 Новый лид «Больше одного»",
        f"Клиника: {lead.clinic_name}",
        f"Контакт: {lead.full_name}, {lead.phone}",
    ]
    if lead.doctors_count_range:
        lines.append(f"Врачей: {lead.doctors_count_range}")
    if lead.comment:
        lines.append(f"Комментарий: {lead.comment}")
    return "\n".join(lines)
