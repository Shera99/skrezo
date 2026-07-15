"""Нотация зубов FDI и её валидация.

FDI: двузначный код «квадрант + номер зуба». Постоянные — квадранты 1–4, зубы
1–8 (11–18, 21–28, 31–38, 41–48). Молочные — квадранты 5–8, зубы 1–5 (51–55,
61–65, 71–75, 81–85).
"""

from __future__ import annotations

_PERMANENT = {q * 10 + t for q in (1, 2, 3, 4) for t in range(1, 9)}
_PRIMARY = {q * 10 + t for q in (5, 6, 7, 8) for t in range(1, 6)}
VALID_TEETH = frozenset(_PERMANENT | _PRIMARY)


def is_valid_tooth(number: int) -> bool:
    """Является ли число корректным номером зуба FDI."""
    return number in VALID_TEETH
