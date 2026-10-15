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
from functools import lru_cache

#: Пробел-разделитель тысяч («2 500», в т.ч. неразрывный) — часть записи числа.
#: Ровно три цифры после пробела обязательны: иначе «04 10:00» склеивалось бы
#: в несуществующее число 0410 и ломало разбор даты со временем.
_INNER_SPACE = re.compile("(?<=\\d)[\\s\u00a0\u202f](?=\\d{3}\\b)")
_DIGITS = re.compile(r"\d+")
#: Границы — «не цифра», а не ``\b``: в слоте инструмента время стоит сразу за
#: буквой (``2026-10-14T09:30:00+06:00``), и ``\b`` перед ``09`` не находился.
#: Тогда из слота «виделось» только смещение пояса ``06:00``, а верное время
#: 09:30 валидатор считал выдуманным и блокировал ответ (V02).
_TIME = re.compile(r"(?<!\d)([0-2]?\d):([0-5]\d)(?!\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DOT_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\b")

#: Имя врача узнаём по обращению: «врач Асанова», «к доктору Иванову».
#: Без ключевого слова имён не ищем — иначе любое слово с большой буквы
#: (город, название клиники, «Здравствуйте») стало бы «именем врача».
_DOCTOR_MENTION = re.compile(r"(?i:врач|доктор|дарыгер)\w*\s+((?:[А-ЯЁ][а-яё]+\s*){1,3})")

#: Слово названия — буквы и цифры подряд: кавычки, дефисы и пробелы между
#: словами в названии клиники и в ответе модели бывают любыми.
_WORD = re.compile(r"\w+")

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


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


@lru_cache(maxsize=1024)
def _whole_words(name: str) -> re.Pattern[str]:
    """Название целыми словами: «улыбка» не находится в «улыбкой», «dent» — в «dental»."""
    body = r"\W+".join(re.escape(word) for word in name.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


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
    #: Названия ЧУЖИХ клиник платформы (V09): упоминание любого из них в
    #: ответе — утечка или галлюцинация, и то и другое наружу не уходит.
    foreign_clinics: frozenset[str] = frozenset()
    #: Название СВОЕЙ клиники: цифры в нём («123456», «Дентал 365») — не цена
    #: и не время, и V05 не должен блокировать из-за них простое приветствие.
    own_clinic: str | None = None

    @classmethod
    def build(
        cls,
        sources: Iterable[str],
        doctors: Iterable[str],
        foreign_clinics: Iterable[str] = (),
        own_clinic: str | None = None,
    ) -> TurnFacts:
        doctor_names = [name for name in doctors if name]
        numbers: set[str] = set()
        times: set[str] = set()
        dates: set[str] = set()
        # Название своей клиники — тоже факт: цифры в нём («123456», «365») модель
        # вправе произнести, и V01 не должен принимать их за выдуманную цену.
        for source in [*sources, own_clinic or ""]:
            if not source:
                continue
            numbers |= _numbers(source)
            times |= _times(source)
            dates |= _dates(source)
        return cls(
            numbers=frozenset(numbers),
            times=frozenset(times),
            dates=frozenset(dates),
            doctors=frozenset(name.lower() for name in doctor_names),
            foreign_clinics=_foreign_names(foreign_clinics, [own_clinic or "", *doctor_names]),
            own_clinic=(own_clinic or "").strip() or None,
        )

    def without_own_clinic(self, text: str) -> str:
        """Текст ответа без названия своей клиники — для всех проверок содержания.

        Название — не утверждение бота: цифры в нём не цена (V01/V05), «Доктор
        Айбек» — не врач (V03), «Дентал Плюс» — не чужая клиника «Дентал» (V09).
        """
        if not self.own_clinic:
            return text
        # Пробелы внутри названия — любые: модель пишет «Доктор Айбек» и с
        # неразрывным пробелом, и с переносом строки.
        pattern = r"\s+".join(re.escape(word) for word in self.own_clinic.split())
        return re.sub(pattern, " ", text, flags=re.IGNORECASE)

    # ── Проверки, которые зовёт валидатор ───────────────────────────────────

    def unknown_numbers(self, text: str) -> set[str]:
        return _numbers(text) - self.numbers

    def unknown_times(self, text: str) -> set[str]:
        return (_times(text) - self.times) | (_dates(text) - self.dates)

    def foreign_mentions(self, text: str) -> set[str]:
        """Названия чужих клиник, найденные в ответе целыми словами (V09)."""
        return {name for name in self.foreign_clinics if _whole_words(name).search(text)}

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


def _foreign_names(names: Iterable[str], own: Iterable[str]) -> frozenset[str]:
    """Названия чужих клиник, которые стоит искать в ответе (V09).

    Отсеиваются названия, целиком составленные из своих слов — названия своей
    клиники и имён своих врачей. Инцидент 13.10.2026: на платформе есть
    клиника «Aibek», и каждый ответ «запись к врачу Aibek» снимался как
    упоминание чужой клиники. Отличить своего врача от чужой клиники с тем же
    именем по тексту нельзя, а свой врач в ответе — норма.

    Короткие названия (меньше пяти букв) тоже не ищем: «Дент» встретится в
    обычной фразе.
    """
    own_words = {word for name in own for word in _words(name)}
    found: set[str] = set()
    for raw in names:
        words = _words(raw or "")
        name = " ".join(words)
        if len(name) < 5 or set(words) <= own_words:
            continue
        found.add(name)
    return frozenset(found)
