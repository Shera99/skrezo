"""Шаблоны напоминаний Skrezo — в том виде, в каком их регистрирует Meta.

Текст шаблона хранит Meta, сервер шлёт только имя, язык, значения переменных
по порядку и payload кнопок. Поэтому состав здесь обязан совпадать с тем, что
шлют отправители:

* ``visit_reminder`` — :class:`~app.modules.ai.application.visits.VisitReminderService`:
  имя, дата ДД.ММ, время ЧЧ:ММ, клиника; кнопки «Приду» / «Перенести»;
* ``debt_reminder`` — :mod:`app.modules.ai.application.patient_reminders`:
  имя, сумма в сомах, клиника; без кнопок;
* ``recall_reminder`` — там же: имя, число месяцев, клиника; кнопки
  «Записаться» / «Не сейчас».

Переменные в тексте идут по возрастанию номера, и ни одна не стоит в самом
начале или конце текста — иначе Meta отклоняет шаблон. Кыргызские тексты долга
и повторного осмотра перед отправкой в Meta стоит показать носителю языка.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.shared.whatsapp_cloud import TemplateDefinition

#: Коды языков Meta. Сервер выбирает перевод по началу кода (``ru``, ``ky``).
RU = "ru"
KY = "ky_KG"


@dataclass(frozen=True, slots=True)
class _Text:
    """Перевод шаблона: текст с {{n}} и подписи кнопок по порядку."""

    body: str
    buttons: tuple[str, ...] = ()


_VISIT = {
    RU: _Text(
        "Здравствуйте, {{1}}! Напоминаем о приёме {{2}} в {{3}} в клинике {{4}}. "
        "Нажмите «Приду», если всё в силе, или «Перенести», чтобы выбрать другое время.",
        ("Приду", "Перенести"),
    ),
    KY: _Text(
        "Саламатсызбы, {{1}}! Эскертебиз: {{2}} саат {{3}} сизди {{4}} күтөт. "
        "Келсеңиз — «Келем», башка убакыт тандоо үчүн «Которуу» баскычын басыңыз.",
        ("Келем", "Которуу"),
    ),
}
_DEBT = {
    RU: _Text(
        "Здравствуйте, {{1}}! Напоминаем: осталось оплатить {{2}} сом за лечение "
        "в клинике {{3}}. Спасибо!"
    ),
    KY: _Text(
        "Саламатсызбы, {{1}}! Эскертебиз: {{2}} сом төлөнө элек, бул {{3}} "
        "клиникасындагы дарылоо үчүн. Рахмат!"
    ),
}
_RECALL = {
    RU: _Text(
        "Здравствуйте, {{1}}! Прошло {{2}} месяцев с вашего визита в клинику {{3}} — "
        "приглашаем на повторный осмотр. Нажмите «Записаться», и мы подберём время.",
        ("Записаться", "Не сейчас"),
    ),
    KY: _Text(
        "Саламатсызбы, {{1}}! Акыркы келгениңизден бери {{2}} ай өттү — {{3}} "
        "клиникасы сизди кайра текшерүүгө чакырат. Убакыт тандоо үчүн «Жазылуу» "
        "баскычын басыңыз.",
        ("Жазылуу", "Азыр эмес"),
    ),
}


def _definition(
    name: str,
    category: str,
    language: str,
    text: _Text,
    example: list[str],
    *,
    buttons: bool,
) -> TemplateDefinition:
    components: list[dict[str, object]] = [
        {"type": "BODY", "text": text.body, "example": {"body_text": [example]}}
    ]
    if buttons and text.buttons:
        components.append(
            {
                "type": "BUTTONS",
                "buttons": [{"type": "QUICK_REPLY", "text": title} for title in text.buttons],
            }
        )
    return TemplateDefinition(
        name=name,
        language=language,
        category=category,
        status="",
        components=tuple(components),
    )


def reminder_templates(
    *,
    visit_name: str,
    debt_name: str,
    recall_name: str,
    visit_buttons: bool,
    recall_buttons: bool,
    recall_months: int,
    languages: tuple[str, ...] = (RU, KY),
) -> list[TemplateDefinition]:
    """Три шаблона напоминаний на нужных языках — тела запросов создания в Meta.

    Имена и наличие кнопок — из настроек: шаблон должен совпасть с тем, что
    шлёт сервер. Без кнопок у отправителя кнопок нет и в шаблоне — лишняя кнопка
    ломает отправку так же, как недостающая.
    """
    plan = (
        (visit_name, "UTILITY", _VISIT, ["Айгерим", "28.11", "10:30", "Улыбка"], visit_buttons),
        (debt_name, "UTILITY", _DEBT, ["Айгерим", "2 500", "Улыбка"], False),
        (
            recall_name,
            "MARKETING",
            _RECALL,
            ["Айгерим", str(recall_months), "Улыбка"],
            recall_buttons,
        ),
    )
    return [
        _definition(name, category, language, texts[language], example, buttons=buttons)
        for name, category, texts, example, buttons in plan
        for language in languages
        if name.strip()
    ]
