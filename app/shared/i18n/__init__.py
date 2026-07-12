"""Мультиязычность: резолв языка запроса и каталог серверных строк."""

from __future__ import annotations

from app.shared.i18n.lang import get_request_lang, resolve_lang
from app.shared.i18n.messages import translate

__all__ = ["get_request_lang", "resolve_lang", "translate"]
