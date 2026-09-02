"""Валидатор ответов ассистента (§7 ТЗ) — детерминированный код до отправки.

Работает всегда: не зависит от модели, промпта и настроения провайдера.
Инкремент 1 несёт правила, не требующие сверки чисел с журналом инструментов
(V01–V03 придут вместе с инструментами записи):

* V04 — запрещённые утверждения (гарантии, «не больно», препараты);
* V05 — конкретика (числа, время) при пустом ``used_facts``;
* V06 — ``confidence=low`` / ``needs_human`` → эскалация;
* V07 — язык ответа не совпал с языком диалога → одна перегенерация;
* V08 — длина > 1000 символов → одна перегенерация.

⚠️ Стоп-лист V04 — черновик: калибруется в теневом режиме на живом трафике
(слишком жёсткий даст ложные срабатывания, слишком мягкий пропустит обещание).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.ai.domain.reply import BotReply


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


#: Запрещённые шаблоны (§7.2): гарантии результата, обещание «не больно»,
#: препараты. Диагнозы намеренно НЕ здесь — «кариес» и «пульпит» встречаются
#: в названиях услуг прайса, их отлов требует калибровки на живом трафике.
_FORBIDDEN: list[tuple[str, re.Pattern[str]]] = [
    ("guarantee", re.compile(r"\bгарантир\w*|точно\s+вылечим|обязательно\s+поможет", re.I)),
    ("painless", re.compile(r"не\s+больно|безболезнен\w*|не\s+почувству\w*|оорубайт", re.I)),
    (
        "meds",
        re.compile(
            r"\b(анальгин|ибупрофен|кетанов|нурофен|парацетамол|антибиотик\w*)\b", re.I
        ),
    ),
]

#: Конкретика: числа от 3 цифр (цены) и время HH:MM — при пустых фактах блок.
_SPECIFICS = re.compile(r"\d{3,}|\b\d{1,2}:\d{2}\b")

_MAX_REPLY_LEN = 1000


def validate(reply: BotReply, conversation_lang: str) -> Verdict:
    """Проверить ответ; порядок правил — от блокирующих к мягким."""
    codes: list[str] = []
    for label, pattern in _FORBIDDEN:
        if pattern.search(reply.reply):
            codes.append("V04")
            return Verdict(VerdictAction.BLOCK, codes, notes=f"forbidden:{label}")
    if not reply.used_facts and _SPECIFICS.search(reply.reply):
        return Verdict(VerdictAction.BLOCK, ["V05"], notes="конкретика без used_facts")
    if reply.confidence == "low" or reply.needs_human:
        return Verdict(VerdictAction.ESCALATE, ["V06"], notes=reply.human_reason)
    if reply.lang != conversation_lang:
        return Verdict(VerdictAction.REGENERATE, ["V07"], notes=f"{reply.lang}≠{conversation_lang}")
    if len(reply.reply) > _MAX_REPLY_LEN:
        return Verdict(VerdictAction.REGENERATE, ["V08"])
    return Verdict(VerdictAction.PASS)
