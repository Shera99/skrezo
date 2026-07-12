"""Определение языка запроса.

Приоритет: явный ``?lang=`` → заголовок ``Accept-Language`` → дефолт (``ru``).
Для авторизованных запросов язык аккаунта подмешивается на уровне ручки.
"""

from __future__ import annotations

from fastapi import Header, Query

from app.shared.db.enums import DEFAULT_LANG, Lang

_SUPPORTED = {lang.value for lang in Lang}


def _match(candidate: str | None) -> Lang | None:
    """Сопоставить строку (``ru``, ``ru-RU``) поддерживаемому языку."""
    if not candidate:
        return None
    code = candidate.strip().split("-", 1)[0].lower()
    return Lang(code) if code in _SUPPORTED else None


def resolve_lang(query_lang: str | None, accept_language: str | None) -> Lang:
    """Выбрать язык из query-параметра или заголовка ``Accept-Language``."""
    if matched := _match(query_lang):
        return matched
    # Accept-Language: "ru-RU,ru;q=0.9,en;q=0.8" — берём первый подходящий.
    for part in (accept_language or "").split(","):
        token = part.split(";", 1)[0]
        if matched := _match(token):
            return matched
    return DEFAULT_LANG


def get_request_lang(
    lang: str | None = Query(default=None, description="Язык ответа: kg|kz|ru|en"),
    accept_language: str | None = Header(default=None),
) -> Lang:
    """FastAPI-зависимость: язык текущего запроса."""
    return resolve_lang(lang, accept_language)
