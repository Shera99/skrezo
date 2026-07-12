"""Юнит-тесты резолва языка и каталога сообщений (без БД)."""

from __future__ import annotations

from app.shared.db.enums import Lang
from app.shared.i18n.lang import resolve_lang
from app.shared.i18n.messages import translate


def test_query_param_has_priority() -> None:
    assert resolve_lang("en", "ru-RU,ru;q=0.9") == Lang.EN


def test_accept_language_parsed() -> None:
    assert resolve_lang(None, "kg-KG,kg;q=0.9,ru;q=0.8") == Lang.KG


def test_default_and_unsupported() -> None:
    assert resolve_lang(None, None) == Lang.RU
    assert resolve_lang("fr", "fr-FR") == Lang.RU  # неподдерживаемый → дефолт


def test_translate_with_fallback() -> None:
    assert translate("role.owner", Lang.EN) == "Owner"
    assert translate("role.owner", Lang.RU) == "Владелец"
    # неизвестный ключ → возвращаем сам ключ
    assert translate("nope.key", Lang.RU) == "nope.key"
