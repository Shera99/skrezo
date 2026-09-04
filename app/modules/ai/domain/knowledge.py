"""Доменные правила базы знаний и пробелов (Ф6).

* нормализация вопроса — чтобы «Есть ли у вас парковка?!» и «есть ли у вас
  парковка» стали одной строкой очереди;
* запрет цен в базе знаний — цены живые и берутся инструментом из прайса;
  продублированная в документе цена рано или поздно назовётся устаревшей.
"""

from __future__ import annotations

import re

_DROP = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
#: Похоже на цену: число от трёх цифр (с пробелами-разделителями) и валюта рядом.
_PRICE = re.compile(r"\d[\d\s  ]{2,}\s*(сом|som|kgs|тыйын|тыйин|сомго|сомдон)\b", re.IGNORECASE)

#: Короче — не вопрос, а междометие: «да», «ок», «?».
MIN_NORMALIZED_LEN = 4
#: Порог триграммной близости для склейки формулировок (0..1).
SIMILARITY_THRESHOLD = 0.55


def normalize_question(text: str) -> str:
    """Регистр, ё→е, без пунктуации, один пробел; обрезано до 300 символов."""
    lowered = text.lower().replace("ё", "е")
    plain = _DROP.sub(" ", lowered)
    return _SPACES.sub(" ", plain).strip()[:300]


def mentions_price(text: str) -> bool:
    """Есть ли в тексте цена — такому в базе знаний не место."""
    return _PRICE.search(text) is not None
