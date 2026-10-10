"""Бизнес-правила заявок клиник (без внешних зависимостей)."""

from __future__ import annotations

from app.shared.db.enums import DoctorsCountRange


def parse_doctors_count_range(raw: str | None) -> DoctorsCountRange | None:
    """Свободный текст «сколько врачей» → диапазон заявки.

    Форма лендинга присылает произвольную строку; в очередь она попадает, только
    если после обрезки пробелов это ровно ``2-5`` | ``6-10`` | ``10+``. Всё
    остальное («2-3», «пять», пустая строка) — ``None``: придумывать диапазон за
    человека нельзя, менеджер уточнит на звонке.
    """
    if raw is None:
        return None
    try:
        return DoctorsCountRange(raw.strip())
    except ValueError:
        return None
