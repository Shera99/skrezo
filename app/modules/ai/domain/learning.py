"""Правила регрессии из выученных ответов (Ф6).

Кейс — вопрос пациента и ожидание, зависящее от вида. Сравнивать ответ модели
с эталоном дословно нельзя (модель перефразирует), поэтому ожидание — ключевые
слова эталона: ответ засчитывается, если содержит хотя бы половину их основ.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.modules.ai.domain.knowledge import normalize_question

CASE_KINDS = frozenset({"knowledge", "reply", "refusal", "medical"})
RUN_MODES = frozenset({"offline", "model"})

_WORD = re.compile(r"[a-zа-яёңөү]{5,}", re.IGNORECASE)
#: Частые слова обоих языков, по которым ответ не узнать.
_STOP = frozenset(
    {
        "который",
        "которые",
        "которая",
        "можно",
        "нужно",
        "будет",
        "также",
        "после",
        "перед",
        "через",
        "чтобы",
        "этого",
        "более",
        "менее",
        "очень",
        "всегда",
        "только",
        "пожалуйста",
        "здравствуйте",
        "спасибо",
        "клиника",
        "клиники",
        "клинике",
        "клинику",
        "пациент",
        "пациента",
        "пациенту",
        "приём",
        "приёма",
        "приема",
        "администратор",
        "администратора",
        "уточните",
        "уточню",
        "подскажите",
        "болот",
        "керек",
        "кийин",
        "менен",
        "болуп",
        "биздин",
        "сиздин",
        "сизге",
        "саламатсызбы",
        "рахмат",
        "клиникада",
        "клиникага",
        "бейтап",
    }
)
_MAX_KEYWORDS = 8


def _stem(word: str) -> str:
    """Грубая основа: без двух последних букв — падежи и числа не мешают."""
    return word[: max(5, len(word) - 2)]


def extract_keywords(text: str, limit: int = _MAX_KEYWORDS) -> list[str]:
    """Содержательные слова эталона (≥5 букв, без стоп-слов, без повторов основ)."""
    seen: set[str] = set()
    keywords: list[str] = []
    for word in _WORD.findall(normalize_question(text)):
        if word in _STOP:
            continue
        stem = _stem(word)
        if stem in seen:
            continue
        seen.add(stem)
        keywords.append(word)
        if len(keywords) >= limit:
            break
    return keywords


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Какие ключевые слова (по основе) встретились в ответе."""
    haystack = normalize_question(text)
    return [word for word in keywords if _stem(word.lower().replace("ё", "е")) in haystack]


def keywords_pass(hits: int, total: int) -> bool:
    """Половина ключевых слов — порог узнавания эталона."""
    return total == 0 or hits >= math.ceil(total / 2)


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Итог кейса: passed | failed | skipped и что ожидали / получили."""

    status: str
    expected: str
    actual: str
    note: str | None = None


def evaluate_keywords(reply_text: str, keywords: list[str], *, expected_label: str) -> Evaluation:
    hits = keyword_hits(reply_text, keywords)
    ok = keywords_pass(len(hits), len(keywords))
    return Evaluation(
        "passed" if ok else "failed",
        expected_label,
        reply_text[:500],
        note=f"ключевые слова: {len(hits)} из {len(keywords)}",
    )
