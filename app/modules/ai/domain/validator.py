"""Валидатор ответов ассистента (§7 ТЗ) — детерминированный код до отправки.

Работает всегда: не зависит от модели, промпта и настроения провайдера.

* V01–V03 — цена, дата/время и имя врача сверяются с фактами хода
  (:mod:`app.modules.ai.domain.facts`): назвать можно только то, что модели
  показали. Это единственная защита от выдуманной цены — сама модель о своей
  выдумке не сообщит;
* V04 — запрещённые утверждения (гарантии, «не больно», препараты);
* V09 — упоминание чужой клиники: блок и оповещение нам;
* V05 — конкретика (числа, время) при пустом ``used_facts``;
* V06 — ``confidence=low`` / ``needs_human`` → эскалация;
* V07 — язык ответа не совпал с языком диалога → одна перегенерация;
* V08 — длина > 1000 символов → одна перегенерация.

Название своей клиники из проверок содержания (V01–V05, V09) исключено: это
не утверждение бота, а в названии бывает что угодно — цифры, «Доктор …»,
чужое слово.

Блок V01–V03 и V05 — тоже не мгновенная эскалация: конвейер ответа
(``reply_service``) один раз просит модель переписать ответ с подсказкой, что
именно не сошлось с фактами, и только повторный блок уводит диалог к человеку.
Здесь вердикт остаётся «блок» — решение о повторе принимает конвейер.

⚠️ Стоп-лист V04 — черновик: калибруется в теневом режиме на живом трафике
(слишком жёсткий даст ложные срабатывания, слишком мягкий пропустит обещание).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

from app.modules.ai.domain.facts import TurnFacts
from app.modules.ai.domain.reply import BotReply

logger = structlog.get_logger(__name__)


class VerdictAction(StrEnum):
    PASS = "pass"
    BLOCK = "block"  # не отправлять, эскалация
    ESCALATE = "escalate"  # сам попросил человека / не уверен
    REGENERATE = "regenerate"  # один повтор (язык/длина), затем эскалация


@dataclass(frozen=True, slots=True)
class Verdict:
    action: VerdictAction
    codes: list[str] = field(default_factory=list)
    notes: str | None = None
    #: Коды блока первой попытки, если ответ прошёл со второй (повтор с подсказкой).
    #: Выдумка, исправленная повтором, остаётся фактической ошибкой для лестницы
    #: доверия: модель её совершила, просто пациент её не увидел.
    retried_codes: list[str] = field(default_factory=list)


#: Запрещённые шаблоны (§7.2): гарантии результата, обещание «не больно»,
#: препараты. Диагнозы намеренно НЕ здесь — «кариес» и «пульпит» встречаются
#: в названиях услуг прайса, их отлов требует калибровки на живом трафике.
_FORBIDDEN: list[tuple[str, re.Pattern[str]]] = [
    ("guarantee", re.compile(r"\bгарантир\w*|точно\s+вылечим|обязательно\s+поможет", re.I)),
    ("painless", re.compile(r"не\s+больно|безболезнен\w*|не\s+почувству\w*|оорубайт", re.I)),
    (
        "meds",
        re.compile(r"\b(анальгин|ибупрофен|кетанов|нурофен|парацетамол|антибиотик\w*)\b", re.I),
    ),
]

#: Конкретика: числа от 3 цифр (цены) и время HH:MM — при пустых фактах блок.
_SPECIFICS = re.compile(r"\d{3,}|(?<!\d)\d{1,2}:\d{2}(?!\d)")

_MAX_REPLY_LEN = 1000


def validate(
    reply: BotReply,
    conversation_lang: str | None,
    facts: TurnFacts | None = None,
) -> Verdict:
    """Проверить ответ; порядок правил — от блокирующих к мягким.

    ``conversation_lang=None`` — язык диалога ещё не зафиксирован (первый
    ответ): V07 не применяется, язык определяет сама модель по сообщению
    пациента (§10.2), и мы его запоминаем.

    ``facts=None`` отключает V01–V03: у вызывающего не было фактов хода.
    В боевом конвейере они есть всегда — параметр со значением по умолчанию
    оставлен для точечных проверок отдельных правил.
    """
    # Название своей клиники вырезаем ДО всех проверок содержания: в нём бывает
    # что угодно, и каждое правило видело в нём своё — «123456» было ценой
    # (V05), «Доктор Айбек» — врачом не из справочника (V03), «Дентал Плюс» —
    # чужой клиникой «Дентал» (V09). Приветствие «Я ассистент клиники «…»» из-за
    # этого уходило администратору. Язык (V07) и длина (V08) — по полному тексту.
    text = facts.without_own_clinic(reply.reply) if facts else reply.reply
    # Чужая клиника (V09) и запреты (V04) — раньше сверки фактов: блок по фактам
    # конвейер повторяет, и при обратном порядке выдуманная цена рядом с чужим
    # названием ушла бы на повтор, а сигнал об утечке между клиниками — потерялся.
    if facts is not None:
        foreign = facts.foreign_mentions(text)
        if foreign:
            # V09 — единственное правило, о котором узнаём и мы: упоминание чужой
            # клиники значит либо утечку контекста, либо галлюцинацию промпта.
            logger.error("ai_v09_foreign_clinic", clinics=sorted(foreign)[:3])
            return Verdict(
                VerdictAction.BLOCK, ["V09"], notes=f"чужая клиника: {sorted(foreign)[:2]}"
            )
    for label, pattern in _FORBIDDEN:
        if pattern.search(text):
            return Verdict(VerdictAction.BLOCK, ["V04"], notes=f"forbidden:{label}")
    if facts is not None:
        fact_verdict = _check_facts(text, facts)
        if fact_verdict is not None:
            return fact_verdict
    if not reply.used_facts and _SPECIFICS.search(text):
        return Verdict(VerdictAction.BLOCK, ["V05"], notes="конкретика без used_facts")
    if reply.confidence == "low" or reply.needs_human:
        return Verdict(VerdictAction.ESCALATE, ["V06"], notes=reply.human_reason)
    if conversation_lang is not None and reply.lang != conversation_lang:
        return Verdict(VerdictAction.REGENERATE, ["V07"], notes=f"{reply.lang}≠{conversation_lang}")
    if len(reply.reply) > _MAX_REPLY_LEN:
        return Verdict(VerdictAction.REGENERATE, ["V08"])
    return Verdict(VerdictAction.PASS)


def _check_facts(text: str, facts: TurnFacts) -> Verdict | None:
    """V01–V03: всё конкретное в ответе должно быть среди фактов хода."""
    numbers = facts.unknown_numbers(text)
    if numbers:
        return Verdict(
            VerdictAction.BLOCK, ["V01"], notes=f"цены вне фактов: {sorted(numbers)[:3]}"
        )
    moments = facts.unknown_times(text)
    if moments:
        return Verdict(
            VerdictAction.BLOCK, ["V02"], notes=f"время вне фактов: {sorted(moments)[:3]}"
        )
    doctors = facts.unknown_doctors(text)
    if doctors:
        return Verdict(
            VerdictAction.BLOCK, ["V03"], notes=f"врач не из справочника: {sorted(doctors)[:3]}"
        )
    return None
