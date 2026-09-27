"""Служебные шаблоны Meta — каналу для проверки, пациентам не отправляются.

Meta заводит в каждом WABA тестовый ``hello_world`` («Welcome and
congratulations!! This message demonstrates…») для проверки канала. Он
одобрен и относится к utility, поэтому без фильтра попадал в каталог клиники
и предлагался кабинетом при закрытом окне 24 часов — так тестовый текст
ушёл реальному пациенту. Список имён — ``WA_SERVICE_TEMPLATE_NAMES``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, tzinfo

from app.shared.db.enums import Lang
from app.shared.i18n.messages import translate


def is_service_template(meta_name: str, service_names: Iterable[str]) -> bool:
    """Имя шаблона входит в список служебных (регистр и пробелы не важны)."""
    name = meta_name.strip().lower()
    return any(name == service.strip().lower() for service in service_names)


# Название шаблона и подписи переменных в каталоге кабинета (решение 5 от 26.09):
# у общих шаблонов платформы их пишет команда Skrezo, у шаблонов клиники —
# имя из Meta и сами плейсхолдеры.


def template_title(meta_name: str, title: str | None) -> str:
    """Понятное название для каталога; без заданного — техническое имя Meta."""
    return title.strip() if title and title.strip() else meta_name


def variable_labels(variables: Sequence[object], labels: Sequence[object]) -> list[str]:
    """Подписи переменных по порядку плейсхолдеров; без подписи — сам ``{{n}}``.

    Длина всегда равна числу переменных: подписи, оставшиеся от прежней версии
    шаблона с другим числом переменных, обрезаются, недостающие заполняются.
    """
    return [
        str(labels[i]) if i < len(labels) and str(labels[i]).strip() else f"{{{{{v}}}}}"
        for i, v in enumerate(variables)
    ]


# Статусы Meta, у которых причина — своя строка каталога; прочие не-approved
# статусы получают общую формулировку со значением статуса.
_STATUS_REASON_KEYS = {
    "pending": "whatsapp.template.pending",
    "rejected": "whatsapp.template.rejected",
    "paused": "whatsapp.template.paused",
    "disabled": "whatsapp.template.disabled",
}

_MONTHS_RU_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _format_day(day: date, lang: Lang) -> str:
    """«20 сентября» / «20 September» — без года: причина живёт недели, не годы."""
    months = _MONTHS_EN if lang is Lang.EN else _MONTHS_RU_GENITIVE
    return f"{day.day} {months[day.month - 1]}"


def unavailable_reason(
    status: str, status_changed_at: datetime | None, lang: Lang, tz: tzinfo
) -> str | None:
    """Почему шаблон нельзя отправить, словами для кабинета; ``None`` — можно.

    Отправка разрешена только ``approved`` (как в ``send_template``); остальные
    статусы Meta объясняются готовой фразой, а с известным моментом смены
    статуса — ещё и датой в таймзоне клиники: «На проверке в Meta с 20 сентября».
    """
    normalized = status.strip().lower()
    if normalized == "approved":
        return None
    key = _STATUS_REASON_KEYS.get(normalized)
    reason = (
        translate(key, lang)
        if key is not None
        else translate("whatsapp.template.unavailable", lang).format(status=status)
    )
    if status_changed_at is None:
        return reason
    day = status_changed_at.astimezone(tz).date()
    return translate("whatsapp.template.since", lang).format(
        reason=reason, date=_format_day(day, lang)
    )
