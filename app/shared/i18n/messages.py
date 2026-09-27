"""Каталог серверных строк по языкам (не БД-контент).

Сюда попадают системные тексты, которые нужно отдавать локализованно:
названия системных ролей, шаблоны уведомлений и т.п. Фолбэк — на ``ru``,
затем на сам ключ.

⚠️ Значения kg/kz — базовые, требуют вычитки носителями.
"""

from __future__ import annotations

from app.shared.db.enums import DEFAULT_LANG, Lang

MESSAGES: dict[str, dict[Lang, str]] = {
    # Названия системных ролей (ключ роли -> перевод)
    "role.owner": {Lang.RU: "Владелец", Lang.EN: "Owner", Lang.KG: "Ээси", Lang.KZ: "Иесі"},
    "role.manager": {
        Lang.RU: "Менеджер",
        Lang.EN: "Manager",
        Lang.KG: "Менеджер",
        Lang.KZ: "Менеджер",
    },
    "role.worker": {
        Lang.RU: "Работник",
        Lang.EN: "Worker",
        Lang.KG: "Кызматкер",
        Lang.KZ: "Қызметкер",
    },
    # Причины недоступности шаблона WhatsApp (каталог шаблонов кабинета).
    "whatsapp.template.pending": {
        Lang.RU: "На проверке в Meta",
        Lang.EN: "Under review by Meta",
        Lang.KG: "На проверке в Meta",
        Lang.KZ: "На проверке в Meta",
    },
    "whatsapp.template.rejected": {
        Lang.RU: "Отклонён Meta",
        Lang.EN: "Rejected by Meta",
        Lang.KG: "Отклонён Meta",
        Lang.KZ: "Отклонён Meta",
    },
    "whatsapp.template.paused": {
        Lang.RU: "Приостановлен Meta",
        Lang.EN: "Paused by Meta",
        Lang.KG: "Приостановлен Meta",
        Lang.KZ: "Приостановлен Meta",
    },
    "whatsapp.template.disabled": {
        Lang.RU: "Отключён Meta",
        Lang.EN: "Disabled by Meta",
        Lang.KG: "Отключён Meta",
        Lang.KZ: "Отключён Meta",
    },
    "whatsapp.template.unavailable": {
        Lang.RU: "Недоступен: статус {status}",
        Lang.EN: "Unavailable: status {status}",
        Lang.KG: "Недоступен: статус {status}",
        Lang.KZ: "Недоступен: статус {status}",
    },
    "whatsapp.template.since": {
        Lang.RU: "{reason} с {date}",
        Lang.EN: "{reason} since {date}",
        Lang.KG: "{reason} с {date}",
        Lang.KZ: "{reason} с {date}",
    },
}


def translate(key: str, lang: Lang) -> str:
    """Вернуть строку по ключу и языку (фолбэк ru → сам ключ)."""
    entry = MESSAGES.get(key, {})
    return entry.get(lang) or entry.get(DEFAULT_LANG) or key
