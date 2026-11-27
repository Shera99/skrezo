"""Умение «Ответы в чате» (``answers``, 27.11.2026) — сборка системного промпта.

Выключенные ответы убирают из префикса всё, из чего бот ответил бы на вопрос
о клинике (адрес, телефон, часы, база знаний), и велят передать такой вопрос
администратору. Включённые — промпт байт в байт прежний: кэш префикса у
провайдера у клиник по умолчанию не сбрасывается.
"""

from __future__ import annotations

from app.modules.ai.domain.prompt import ClinicCard, build_system_prompt
from app.modules.ai.domain.skills import DEFAULT_SKILLS, SKILLS, effective_skills
from app.shared.db.enums import AiMode

_CARD = ClinicCard(
    name="Улыбка",
    address="Бишкек, ул. Токтогула, 1",
    phone="+996312000000",
    working_hours="пн–пт 09:00–18:00",
)
_KB = [("Парковка", "Бесплатная парковка во дворе")]
_GLOSSARY = [("тиш", "зуб")]


def _prompt(**caps: str) -> str:
    skills = effective_skills(AiMode.AUTO, caps)
    return build_system_prompt(_CARD, "ru", _GLOSSARY, _KB, skills=skills)


def test_answers_is_on_by_default_and_listed_first() -> None:
    assert SKILLS[0] == "answers" and DEFAULT_SKILLS["answers"] == "auto"


def test_answers_on_keeps_card_and_knowledge() -> None:
    prompt = _prompt()
    assert "Адрес: Бишкек, ул. Токтогула, 1" in prompt
    assert "Телефон: +996312000000" in prompt and "Часы работы: пн–пт" in prompt
    assert "Бесплатная парковка во дворе" in prompt
    assert "ОТВЕТЫ НА ВОПРОСЫ ОТКЛЮЧЕНЫ" not in prompt


def test_default_prompt_is_unchanged_by_the_new_skill() -> None:
    """Ключ ``answers`` в уровнях не меняет промпт клиники по умолчанию."""
    without_skills = build_system_prompt(_CARD, "ru", _GLOSSARY, _KB)
    assert _prompt() == without_skills


def test_answers_off_strips_clinic_facts_and_hands_questions_off() -> None:
    prompt = _prompt(answers="off")
    assert "ОТВЕТЫ НА ВОПРОСЫ ОТКЛЮЧЕНЫ" in prompt and "escalate" in prompt
    assert "Название: Улыбка" in prompt
    for fact in ("Токтогула", "+996312000000", "09:00–18:00", "Бесплатная парковка"):
        assert fact not in prompt, fact
    assert "«тиш» — зуб" in prompt  # глоссарий помогает понять пациента — остаётся
    # Запись и перенос живут дальше, если не выключены сами.
    assert "КАК ВЕСТИ ЗАПИСЬ" in prompt and "ПЕРЕНОС И ОТМЕНА ЗАПИСИ" in prompt


def test_answers_off_with_booking_off_leaves_only_handoff_rules() -> None:
    prompt = _prompt(answers="off", booking="off", changes="off")
    assert "ОТВЕТЫ НА ВОПРОСЫ ОТКЛЮЧЕНЫ" in prompt
    assert "ЗАПИСЬ ЧЕРЕЗ БОТА ОТКЛЮЧЕНА" in prompt
    assert "ПЕРЕНОС И ОТМЕНА ЧЕРЕЗ БОТА ОТКЛЮЧЕНЫ" in prompt
