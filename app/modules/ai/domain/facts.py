"""Факты хода для правил V01–V03 (§7.1) — что модель реально видела.

Валидатор не верит модели на слово: цена, дата, время и имя врача в ответе
должны находиться среди того, что ей показали. Источники факта — ровно три:

* системный промпт хода (карточка клиники, база знаний, глоссарий);
* история диалога (уже проверенные ответы: бот вправе повторить свою цену);
* результаты инструментов этого хода (прайс, слоты, записи пациента).

Набор намеренно щедрый: цель — поймать **выдуманное** число, а не наказать
за пересказ. Придуманная цена не встретится ни в одном источнике.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

#: Пробел-разделитель тысяч («2 500», в т.ч. неразрывный) — часть записи числа.
#: Ровно три цифры после пробела обязательны: иначе «04 10:00» склеивалось бы
#: в несуществующее число 0410 и ломало разбор даты со временем.
_INNER_SPACE = re.compile("(?<=\\d)[\\s\u00a0\u202f](?=\\d{3}\\b)")
_DIGITS = re.compile(r"\d+")
_TIME = re.compile(r"\b([0-2]?\d):([0-5]\d)\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DOT_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\b")

#: Имя врача узнаём по обращению: «врач Асанова», «к доктору Иванову».
#: Без ключевого слова имён не ищем — иначе любое слово с большой буквы
#: (город, название клиники, «Здравствуйте») стало бы «именем врача».
_DOCTOR_MENTION = re.compile(r"(?:врач|доктор|дарыгер)\w*\s+((?:[А-ЯЁ][а-яё]+\s*){1,3})")

#: Числа короче трёх цифр на цену не похожи («40 минут», «2 зуба») — не проверяем.
_PRICE_LIKE_MIN_DIGITS = 3
#: Сравнение имён по основе: русские падежи («Асановой» ≠ «Асанова») иначе
#: давали бы ложные блоки на совершенно верном ответе.
_NAME_STEM = 5


def _normalized(text: str) -> str:
    return _INNER_SPACE.sub("", text)


def _numbers(text: str) -> set[str]:
    """Числа длиной от трёх цифр — кандидаты в цены."""
    body = _normalized(text)
    # Время и даты разбираются своими правилами: «10:00» и «03.09» не цены.
    body = _TIME.sub(" ", body)
    body = _ISO_DATE.sub(" ", body)
    body = _DOT_DATE.sub(" ", body)
    return {n for n in _DIGITS.findall(body) if len(n) >= _PRICE_LIKE_MIN_DIGITS}


def _times(text: str) -> set[str]:
    return {f"{int(h):02d}:{m}" for h, m in _TIME.findall(text)}


def _dates(text: str) -> set[str]:
    found = {f"{int(d):02d}.{int(m):02d}" for _, m, d in _ISO_DATE.findall(text)}
    found |= {f"{int(d):02d}.{int(m):02d}" for d, m in _DOT_DATE.findall(text)}
    return found


def _mentioned_doctors(text: str) -> set[str]:
    names: set[str] = set()
    for chunk in _DOCTOR_MENTION.findall(text):
        names.update(part for part in chunk.split() if part)
    return names


@dataclass(frozen=True, slots=True)
class TurnFacts:
    """Что дозволено назвать в ответе этого хода."""

    numbers: frozenset[str]
    times: frozenset[str]
    dates: frozenset[str]
    doctors: frozenset[str]

    @classmethod
    def build(cls, sources: Iterable[str], doctors: Iterable[str]) -> TurnFacts:
        numbers: set[str] = set()
        times: set[str] = set()
        dates: set[str] = set()
        for source in sources:
            if not source:
                continue
            numbers |= _numbers(source)
            times |= _times(source)
            dates |= _dates(source)
        return cls(
            numbers=frozenset(numbers),
            times=frozenset(times),
            dates=frozenset(dates),
            doctors=frozenset(name.lower() for name in doctors if name),
        )

    # ── Проверки, которые зовёт валидатор ───────────────────────────────────

    def unknown_numbers(self, text: str) -> set[str]:
        return _numbers(text) - self.numbers

    def unknown_times(self, text: str) -> set[str]:
        return (_times(text) - self.times) | (_dates(text) - self.dates)

    def unknown_doctors(self, text: str) -> set[str]:
        return {name for name in _mentioned_doctors(text) if not self._known_name(name)}

    def _known_name(self, name: str) -> bool:
        """Имя есть в справочнике клиники — с поправкой на падежи."""
        token = name.lower()
        for full in self.doctors:
            for word in full.split():
                if word == token:
                    return True
                stem = min(len(word), len(token), _NAME_STEM)
                if stem >= 3 and word[:stem] == token[:stem]:
                    return True
        return False
