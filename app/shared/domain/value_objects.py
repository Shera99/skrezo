"""Общие value-objects домена.

Пока — телефон в формате E.164 (единое правило нормализации для дедупликации
по всем сущностям: ``lead.phone``, ``account.phone`` и т.д.).
"""

from __future__ import annotations

import phonenumbers

from app.core.errors import ValidationAppError

# Регион по умолчанию для номеров без «+» (Кыргызстан).
_DEFAULT_REGION = "KG"


def normalize_phone(raw: str, *, default_region: str = _DEFAULT_REGION) -> str:
    """Привести телефон к каноническому E.164 (``+996700123456``).

    Убирает пробелы/скобки/дефисы и валидирует номер. Бросает
    :class:`ValidationAppError` при некорректном номере — это гарантирует,
    что в БД попадают только сравнимые между собой значения.
    """
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValidationAppError(
            "Некорректный номер телефона",
            code="invalid_phone",
        ) from exc

    if not phonenumbers.is_valid_number(parsed):
        raise ValidationAppError("Некорректный номер телефона", code="invalid_phone")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
