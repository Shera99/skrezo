"""Юнит-тесты передачи человеку и комментария записи (без БД, решения 15.10.2026)."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.modules.ai.application.booking import booked_comment
from app.modules.ai.domain.handoff import next_opening
from app.modules.ai.domain.reply import BotReply
from app.modules.ai.domain.texts import is_handoff_notice, opening_phrase, service_text
from app.modules.ai.domain.validator import VerdictAction, validate
from app.modules.schedule.domain.availability import default_working_hours
from app.modules.schedule.domain.models import WorkingHours

_TZ = ZoneInfo("Asia/Bishkek")
# Среда 14.10.2026; график по умолчанию: пн–сб 09:00–18:00, вс — выходной.
_WEDNESDAY = date(2026, 10, 14)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_TZ)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_at(_WEDNESDAY, 12), None),  # открыто (обед закрытием не считается)
        (_at(_WEDNESDAY, 7, 30), _at(_WEDNESDAY, 9)),  # до открытия — сегодня
        (_at(_WEDNESDAY, 18), _at(date(2026, 10, 15), 9)),  # закрылись — завтра
        (_at(date(2026, 10, 17), 19), _at(date(2026, 10, 19), 9)),  # сб вечер → пн
        (_at(date(2026, 10, 18), 11), _at(date(2026, 10, 19), 9)),  # вс → пн
    ],
)
def test_next_opening_by_default_schedule(now: datetime, expected: datetime | None) -> None:
    assert next_opening(default_working_hours, now) == expected


def test_next_opening_without_working_days_claims_nothing() -> None:
    """Графика нет — «клиника закрыта» не утверждаем."""

    def closed(weekday: int) -> WorkingHours:
        return WorkingHours(weekday, time(9), time(18), None, None, is_working=False)

    assert next_opening(closed, _at(_WEDNESDAY, 12)) is None


@pytest.mark.parametrize(
    ("opens", "lang", "expected"),
    [
        (_at(_WEDNESDAY, 9), "ru", "сегодня с 09:00"),
        (_at(date(2026, 10, 15), 9), "ru", "завтра с 09:00"),
        (_at(date(2026, 10, 19), 10), "ru", "19.10 с 10:00"),
        (_at(date(2026, 10, 15), 9), "ky", "эртең, 09:00"),
    ],
)
def test_opening_phrase(opens: datetime, lang: str, expected: str) -> None:
    assert opening_phrase(opens, lang, today=_WEDNESDAY) == expected


@pytest.mark.parametrize("key", ["handoff", "handoff_asked", "handoff_waiting"])
@pytest.mark.parametrize("lang", ["ru", "ky"])
def test_handoff_notices_are_recognised_with_after_hours_tail(key: str, lang: str) -> None:
    tail = service_text("after_hours", lang).format(when="завтра с 09:00")
    body = service_text(key, lang) + tail

    assert is_handoff_notice(body)
    assert is_handoff_notice(body, waiting_only=True) is (key == "handoff_waiting")


def test_other_service_messages_are_not_handoff_notices() -> None:
    assert not is_handoff_notice("Готово, вы записаны! Консультация — 13.10 13:00.")


@pytest.mark.parametrize("key", ["handoff", "handoff_asked", "handoff_waiting"])
@pytest.mark.parametrize("lang", ["ru", "ky"])
def test_model_repeating_handoff_notice_without_needs_human_escalates(key: str, lang: str) -> None:
    """Модель видит наши шаблоны передачи в истории — повторить их «просто так» нельзя."""
    reply = BotReply(
        reply=service_text(key, lang),
        lang=lang,
        intent="other",
        used_facts=[],
        confidence="high",
        needs_human=False,
        human_reason=None,
    )

    verdict = validate(reply, None)

    assert verdict.action is VerdictAction.ESCALATE
    assert verdict.codes == ["V06"]


def test_booked_comment_carries_patient_words() -> None:
    assert booked_comment(None) == "Запись через WhatsApp-ассистента"
    assert booked_comment("болит 18 зуб") == (
        "Запись через WhatsApp-ассистента. Со слов пациента (записал ассистент): болит 18 зуб"
    )


@pytest.mark.parametrize(
    "text",
    [
        "Администратор уже в курсе по рассрочке, а чистка стоит 2000 сом.",
        "Цену уточните у администратора на месте.",
        "Администратор кабардар, тазалоо 2 000 сом.",
    ],
)
def test_useful_reply_mentioning_admin_is_not_a_handoff(text: str) -> None:
    """«Администратор в курсе» без обещания ответа — обычный ответ, а не передача."""
    reply = BotReply(
        reply=text,
        lang="ru",
        intent="info",
        used_facts=["s"],
        confidence="high",
        needs_human=False,
        human_reason=None,
    )

    assert validate(reply, None).codes == []
