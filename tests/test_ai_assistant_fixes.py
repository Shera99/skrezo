"""Юнит-тесты исправлений ассистента по инцидентам 12.10.2026 — без базы.

Каждый тест — один из случаев живой переписки:

* модель не знала даты и искала слоты в 2023–2024 годах → строка «СЕЙЧАС»;
* время из ISO-слотов (``…T09:30…``) не распознавалось → ложный блок V02;
* «Клиника «123456»» в приветствии считалась ценой → ложный блок V05;
* «Саламатсызбы» после русской переписки получило русский ответ → язык по
  последнему сообщению;
* «сильно болит зуб» останавливало бота → сильная боль отдельно от неотложного;
* исправимый блок уводил к администратору сразу → один повтор с подсказкой.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.modules.ai.application.reply_service import _fix_instruction, _fixable, with_clock
from app.modules.ai.domain.facts import TurnFacts
from app.modules.ai.domain.language import detect_lang
from app.modules.ai.domain.medical import MedicalKind, classify
from app.modules.ai.domain.prompt import clock_line, without_clock
from app.modules.ai.domain.reply import BotReply
from app.modules.ai.domain.texts import slots_phrase
from app.modules.ai.domain.validator import Verdict, VerdictAction, validate
from app.shared.ai import AiMessagePart
from app.shared.db.models.clinic import Clinic

BISHKEK = ZoneInfo("Asia/Bishkek")


def _reply(text: str, *, facts: list[str] | None = None, lang: str = "ru") -> BotReply:
    return BotReply(
        reply=text,
        lang=lang,
        intent="info",
        used_facts=facts or [],
        confidence="high",
        needs_human=False,
        human_reason=None,
    )


# ── Дата в каждом ходе ──────────────────────────────────────────────────────


def test_clock_line_names_weekday_date_time_and_iso_for_tools() -> None:
    """Понедельник 12.10.2026 — словами для пациента и ISO для инструментов."""
    line = clock_line(datetime(2026, 10, 12, 14, 35, tzinfo=BISHKEK))

    assert line == (
        "СЕЙЧАС: понедельник, 12 октября 2026 г., 14:35 (время клиники). "
        "Сегодня — 2026-10-12, завтра — 2026-10-13."
    )


def test_clock_line_rolls_over_month_and_year() -> None:
    """«Завтра» в конце года — 1 января следующего, а не 32 декабря."""
    line = clock_line(datetime(2026, 12, 31, 23, 50, tzinfo=BISHKEK))

    assert "четверг, 31 декабря 2026 г., 23:50" in line
    assert "завтра — 2027-01-01" in line


def test_with_clock_stamps_last_patient_part_in_clinic_time() -> None:
    """Строка «СЕЙЧАС» — в начале последней реплики, по часам клиники, а не UTC."""
    clinic = Clinic(name="Клиника", timezone="Asia/Bishkek")
    parts = [
        AiMessagePart(role="user", text="Здравствуйте"),
        AiMessagePart(role="model", text="Добрый день!"),
        AiMessagePart(role="user", text="На сегодня самое ближайшее время"),
    ]

    stamped = with_clock(parts, clinic, now=datetime(2026, 10, 12, 8, 33, tzinfo=UTC))

    assert stamped[0] == parts[0]  # ранние реплики не трогаем
    assert stamped[-1].text is not None
    assert stamped[-1].text.startswith("СЕЙЧАС: понедельник, 12 октября 2026 г., 14:33")
    assert stamped[-1].text.endswith("На сегодня самое ближайшее время")


# ── V02: время из ISO-слотов ────────────────────────────────────────────────


def test_times_inside_iso_slots_are_facts() -> None:
    """Слот «2026-10-14T09:30:00+06:00» даёт факт 09:30 — время сразу за буквой T."""
    facts = TurnFacts.build(['{"slot": "2026-10-14T09:30:00+06:00"}'], [])

    assert "09:30" in facts.times
    assert "14.10" in facts.dates
    assert facts.unknown_times("Свободно в 09:30") == set()


def test_incident_reply_with_slot_times_passes_v02() -> None:
    """Ответ из инцидента: 09:00, 09:30, 10:00 — все из слотов, блока нет."""
    slots = ", ".join(f'{{"slot": "2026-10-14T{t}:00+06:00"}}' for t in ("09:00", "09:30", "10:00"))
    facts = TurnFacts.build([f'{{"slots": [{slots}]}}'], [])
    reply = _reply("Ближайшая дата — 14 октября: 09:00, 09:30, 10:00.", facts=["slot"])

    assert validate(reply, "ru", facts).action is VerdictAction.PASS


def test_time_outside_slots_is_still_blocked() -> None:
    """Исправление не открывает дыру: время, которого нет в слотах, — блок V02."""
    facts = TurnFacts.build(['{"slot": "2026-10-14T09:30:00+06:00"}'], [])

    verdict = validate(_reply("Приходите в 11:15", facts=["slot"]), "ru", facts)

    assert verdict.action is VerdictAction.BLOCK
    assert verdict.codes == ["V02"]


def test_utc_shift_of_slot_time_is_blocked() -> None:
    """Слот 09:30 по Бишкеку — это 03:30 UTC; «приходите в 03:30» — блок V02.

    Инструменты отдают только местное время (и в метке факта тоже), поэтому
    UTC-час среди фактов не появляется и сдвиг на 6 часов не проходит.
    """
    tool_output = (
        '{"slots": [{"slot": "2026-10-14T09:30:00+06:00", "time": "09:30", '
        '"fact": "slot:d:2026-10-14T09:30:00+06:00"}]}'
    )
    facts = TurnFacts.build([tool_output], [])

    verdict = validate(_reply("Ждём вас 14.10 в 03:30", facts=["slot"]), "ru", facts)

    assert verdict.codes == ["V02"]


def test_clock_line_is_not_a_fact() -> None:
    """Строка «СЕЙЧАС» не узаконивает выдуманное «сегодня в 14:35»."""
    stamped = clock_line(datetime(2026, 10, 12, 14, 35, tzinfo=BISHKEK)) + "\n\nЗапишите"

    assert without_clock(stamped) == "Запишите"
    assert without_clock("Запишите") == "Запишите"
    facts = TurnFacts.build([without_clock(stamped)], [])
    assert validate(_reply("Приходите в 14:35", facts=["x"]), "ru", facts).codes == ["V02"]


# ── V05: название своей клиники ─────────────────────────────────────────────


@pytest.mark.parametrize("name", ["123456", "Дентал 365"])
def test_own_clinic_name_with_digits_is_not_specifics(name: str) -> None:
    """Приветствие с названием клиники проходит, хотя в нём «число»."""
    facts = TurnFacts.build([], [], own_clinic=name)
    reply = _reply(f"Здравствуйте! Я ассистент клиники «{name}». Чем помочь?")

    assert validate(reply, None, facts).action is VerdictAction.PASS


def test_specifics_without_facts_still_blocked_next_to_clinic_name() -> None:
    """Цена рядом с названием клиники — по-прежнему V05, если фактов нет."""
    facts = TurnFacts.build(["Чистка — 2 500 сом"], [], own_clinic="123456")
    reply = _reply("В клинике «123456» чистка стоит 2 500 сом.")

    verdict = validate(reply, None, facts)

    assert verdict.action is VerdictAction.BLOCK
    assert verdict.codes == ["V05"]


# ── Язык по последнему сообщению ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Саламатсызбы", "ky"),  # инцидент: кыргызское приветствие без «своих» букв
        ("Бүгүн барсам болобу?", "ky"),  # буква ү — кыргызский сразу
        ("Тишим ооруп жатат, канча турат?", "ky"),
        ("Здравствуйте", "ru"),
        ("На сегодня самое ближайшее время", "ru"),
        ("Здравствуйте что вы умеете ?", "ru"),
        # Кыргызское имя в русской фразе — фраза остаётся русской.
        ("Я Гүлнара, запишите на завтра", "ru"),
        ("Меня зовут Айгүл", "ru"),
        ("Айбек", None),  # имя — не язык
        ("Айгүл", None),  # ответ на «как вас зовут?» не переводит разговор
        ("Гүлнара Төлөгөнова", None),
        ("Рахмат", None),  # «рахмат» говорят и по-русски
        ("Ок рахмат", None),
        ("Рахмат, жакшы", "ky"),
        ("500", None),
        ("ок", None),
    ],
)
def test_detect_lang(text: str, expected: str | None) -> None:
    assert detect_lang(text) == expected


def test_mixed_message_follows_majority() -> None:
    """«Саламатсызбы, хочу записаться на консультацию» — русских слов больше."""
    assert detect_lang("Саламатсызбы, хочу записаться на консультацию") == "ru"


# ── Сильная боль ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "На консультацию хотел\nУ меня сильно болит зуб",  # инцидент 12.10
        "Очень болит зуб",
        "Болит всю ночь, не сплю",
        "Катуу оору, түндө уктай албадым",
    ],
)
def test_severe_pain_is_acute_pain_not_urgent(text: str) -> None:
    assert classify(text).kind is MedicalKind.ACUTE_PAIN


@pytest.mark.parametrize(
    "text",
    ["Сильно болит зуб и опухла щека", "Сильная боль и температура 38", "Сильно болит, кровь"],
)
def test_severe_pain_with_red_flags_stays_urgent(text: str) -> None:
    """Отёк, температура, кровь — неотложное, даже рядом с «сильной болью»."""
    assert classify(text).kind is MedicalKind.URGENT


@pytest.mark.parametrize(
    "text",
    [
        "Очень большое спасибо!",
        "очень большая просьба перезвонить",
        "Очень больно удалять зуб мудрости?",  # вопрос о процедуре — его ведёт модель
        "Сильно болеть будет после удаления?",
    ],
)
def test_words_like_pain_are_not_acute_pain(text: str) -> None:
    """«Очень большое спасибо» — не повод писать «сочувствую» и звать администратора."""
    assert classify(text).kind is not MedicalKind.ACUTE_PAIN


def test_plain_pain_is_still_a_complaint() -> None:
    assert classify("У меня болит зуб уже неделю").kind is MedicalKind.COMPLAINT


# ── Фраза о ближайшем времени ───────────────────────────────────────────────


def test_slots_phrase_today_tomorrow_and_date() -> None:
    today = datetime(2026, 10, 12, tzinfo=BISHKEK).date()

    def at(day: int, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 10, day, hour, minute, tzinfo=BISHKEK)

    assert slots_phrase([at(12, 15), at(12, 15, 30)], "ru", today=today) == "сегодня в 15:00, 15:30"
    assert slots_phrase([at(13, 9)], "ru", today=today) == "завтра в 09:00"
    assert slots_phrase([at(15, 10)], "ru", today=today) == "15.10 в 10:00"
    assert slots_phrase([at(12, 15)], "ky", today=today) == "бүгүн саат 15:00"
    assert slots_phrase([], "ru", today=today) == ""


# ── Повтор вместо мгновенной передачи человеку ──────────────────────────────


def test_fact_blocks_are_fixable() -> None:
    facts = TurnFacts.build(['{"slot": "2026-10-14T09:30:00+06:00"}'], [])

    verdict = validate(_reply("Приходите в 11:15", facts=["slot"]), "ru", facts)

    assert verdict.codes == ["V02"]
    assert _fixable(verdict)
    assert not _fixable(Verdict(VerdictAction.ESCALATE, ["V06"]))


def test_foreign_clinic_wins_over_invented_price_and_is_not_retried() -> None:
    """Выдуманная цена рядом с чужой клиникой — V09, а не V01: утечку не прячет повтор."""
    facts = TurnFacts.build(["Чистка — 2 500 сом"], [], ["Стоматология Аврора"])
    reply = _reply("В «Стоматология Аврора» чистка за 9 900 сом.", facts=["s"])

    verdict = validate(reply, "ru", facts)

    assert verdict.codes == ["V09"]
    assert not _fixable(verdict)


def test_forbidden_promise_wins_over_invented_price() -> None:
    """«Не больно» рядом с выдуманной ценой — V04, повтора не будет."""
    facts = TurnFacts.build(["Удаление — 1 500 сом"], [])
    reply = _reply("Удаление за 9 900 сом, это не больно.", facts=["s"])

    verdict = validate(reply, "ru", facts)

    assert verdict.codes == ["V04"]
    assert not _fixable(verdict)


def test_fix_instruction_names_the_problem() -> None:
    instruction = _fix_instruction(
        Verdict(VerdictAction.BLOCK, ["V02"], "время вне фактов: ['09:30']")
    )

    assert "['09:30']" in instruction
    assert "поля time" in instruction
    assert instruction.endswith("Верни строго один JSON.")
