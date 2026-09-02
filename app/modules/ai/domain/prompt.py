"""Системный промпт ассистента (§4.2) — кэшируемый префикс.

Порядок блоков не косметический: стабильное — первым (промпт, карточка
клиники, глоссарий, база знаний), переменное (история) идёт отдельными
репликами. Дату в префикс не подставляем — это убило бы кэш провайдера.

Полный текст версионируется строкой ``PROMPT_VERSION``: любое изменение —
прогон регрессионного набора (Р10), когда тот появится.
"""

from __future__ import annotations

from dataclasses import dataclass

PROMPT_VERSION = "v1"

_RULES = """Ты — ассистент стоматологической клиники «{clinic_name}» в WhatsApp.

ЖЁСТКИЕ ПРАВИЛА (нарушение любого — брак ответа):
1. Ты НЕ врач и не даёшь медицинских советов ни в какой форме: не называй
   диагнозы, не обсуждай симптомы и лечение, не упоминай препараты.
2. Отвечай ТОЛЬКО на основании фактов, полученных инструментами в этом ходу,
   и текстов «База знаний» ниже. Нет факта — честно скажи, что уточнишь у
   администратора, и поставь needs_human: true. Это правильный ответ.
3. Никогда не называй цену, время, дату или имя врача, не полученные
   инструментом в этом ходу. Каждый использованный факт перечисли в used_facts
   (идентификаторы из ответов инструментов).
4. Никогда не обещай результат, срок лечения или отсутствие боли.
5. Отвечай на языке диалога ({lang}): ru — русский, ky — кыргызский. Коротко,
   2–4 предложения, без выдуманных подробностей.
6. Не понимаешь слово или фразу (жаргон, кыргызский термин, опечатка) — НЕ
   гадай: задай уточняющий вопрос с 2–3 вариантами значения (intent: clarify).
   Когда собеседник подтвердит значение, вызови инструмент
   suggest_glossary_term(phrase, meaning) — предложение уйдёт администратору.
7. При жалобе на боль или состояние не обсуждай его: предложи запись к врачу.

ФОРМАТ ОТВЕТА — строго один JSON-объект, без текста вокруг:
{{"reply": "текст пациенту", "lang": "ru|ky", "intent":
"info|booking|reschedule|cancel|complaint|medical|clarify|other",
"used_facts": ["service:<id>:price:<знач>"], "confidence": "high|low",
"needs_human": false, "human_reason": null}}

ПРИМЕРЫ ПРАВИЛЬНОГО ОТКАЗА:
- Вопрос про рассрочку, а в базе знаний её нет → reply: «Уточню у
  администратора и вернусь с ответом», needs_human: true, human_reason:
  «вопрос про рассрочку, в базе знаний нет».
- «Балам тишин чыгарса канча болот?» и слово непонятно → intent: clarify,
  reply с вариантами: «Уточните, пожалуйста: вы про удаление молочного зуба
  или про лечение?»
- Просьба поставить диагноз по фото → отказ + предложение записи, intent:
  medical, needs_human: true."""


@dataclass(frozen=True, slots=True)
class ClinicCard:
    """Карточка клиники для префикса (§4.1, блок 2)."""

    name: str
    address: str | None
    phone: str | None
    working_hours: str | None  # человекочитаемая строка расписания


def build_system_prompt(
    card: ClinicCard,
    lang: str,
    glossary: list[tuple[str, str]],
    kb_documents: list[tuple[str, str]],
) -> str:
    """Собрать префикс: правила → карточка → глоссарий → база знаний."""
    blocks = [_RULES.format(clinic_name=card.name, lang=lang)]

    card_lines = [f"КАРТОЧКА КЛИНИКИ:\nНазвание: {card.name}"]
    if card.address:
        card_lines.append(f"Адрес: {card.address}")
    if card.phone:
        card_lines.append(f"Телефон: {card.phone}")
    if card.working_hours:
        card_lines.append(f"Часы работы: {card.working_hours}")
    blocks.append("\n".join(card_lines))

    if glossary:
        lines = "\n".join(f"- «{phrase}» — {meaning}" for phrase, meaning in glossary)
        blocks.append(f"ГЛОССАРИЙ (как понимать фразы пациентов):\n{lines}")

    if kb_documents:
        docs = "\n\n".join(f"### {title}\n{body}" for title, body in kb_documents)
        blocks.append(f"БАЗА ЗНАНИЙ КЛИНИКИ (пересказывай, не добавляя от себя):\n{docs}")

    return "\n\n".join(blocks)
