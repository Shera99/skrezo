"""Доменные правила базы знаний и пробелов (Ф6).

* нормализация вопроса — чтобы «Есть ли у вас парковка?!» и «есть ли у вас
  парковка» стали одной строкой очереди;
* запрет цен в базе знаний — цены живые и берутся инструментом из прайса;
  продублированная в документе цена рано или поздно назовётся устаревшей;
* оценка токенов и порог полного контекста (spec_1: до 30 000 токенов база
  уходит в промпт целиком, дальше — подбор документов).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

_DROP = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
#: Похоже на цену: число от трёх цифр (с пробелами-разделителями) и валюта рядом.
_PRICE = re.compile(r"\d[\d\s  ]{2,}\s*(сом|som|kgs|тыйын|тыйин|сомго|сомдон)\b", re.IGNORECASE)

#: Короче — не вопрос, а междометие: «да», «ок», «?».
MIN_NORMALIZED_LEN = 4
#: Порог триграммной близости для склейки формулировок (0..1).
SIMILARITY_THRESHOLD = 0.55

#: Словарь категорий базы знаний (spec_1 §3.4).
KB_CATEGORIES = frozenset({"logistics", "aftercare", "faq", "policy", "payment"})
KB_LANGS = frozenset({"ru", "ky"})
#: Порог полного контекста: до него база уходит в промпт целиком.
KB_TOKEN_LIMIT = 30_000
#: Грубая оценка для кириллицы у моделей Gemini — символов на токен.
KB_CHARS_PER_TOKEN = 3
#: Потолок промпта в символах — та же величина, что и на экране редактора.
KB_PROMPT_MAX_CHARS = KB_TOKEN_LIMIT * KB_CHARS_PER_TOKEN


def normalize_question(text: str) -> str:
    """Регистр, ё→е, без пунктуации, один пробел; обрезано до 300 символов."""
    lowered = text.lower().replace("ё", "е")
    plain = _DROP.sub(" ", lowered)
    return _SPACES.sub(" ", plain).strip()[:300]


def mentions_price(text: str) -> bool:
    """Есть ли в тексте цена — такому в базе знаний не место."""
    return _PRICE.search(text) is not None


def estimate_tokens(text: str) -> int:
    """Оценка токенов по длине: точный счёт стоил бы вызова модели на каждый ввод."""
    return math.ceil(len(text) / KB_CHARS_PER_TOKEN)


def fits_in_prompt(lengths: Iterable[int], *, max_chars: int = KB_PROMPT_MAX_CHARS) -> list[bool]:
    """Какие документы (в порядке создания) помещаются в промпт целиком.

    Единое правило для сборки промпта и для отметки «в промпте» в редакторе:
    первые документы, пока сумма длин не превысила потолок; дальше — нет.
    """
    used = 0
    flags: list[bool] = []
    for length in lengths:
        used += length
        flags.append(used <= max_chars)
    return flags
