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
}


def translate(key: str, lang: Lang) -> str:
    """Вернуть строку по ключу и языку (фолбэк ru → сам ключ)."""
    entry = MESSAGES.get(key, {})
    return entry.get(lang) or entry.get(DEFAULT_LANG) or key
