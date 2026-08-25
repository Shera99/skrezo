"""Каталог текстов пуш-уведомлений — редактируемый вручную конфиг.

Это НЕ бизнес-логика: словарь ниже правит человек (владелец продукта),
формулировки в нём — черновые заготовки, а не финальный текст. Как править:

1. Каждому типу пуша (``type``, см. ``docs/PUSH_NOTIFICATIONS.md`` §4) —
   отдельный ключ ``PUSH_TEXTS``, а внутри — по языку (минимум :class:`Lang.RU`
   и :class:`Lang.KG`) три строки: ``title`` (заголовок), ``body`` (текст под
   заголовком), ``speak`` (фраза для чтения ВСЛУХ голосовым ассистентом —
   полное предложение, а не заголовок).
2. Плейсхолдеры — в фигурных скобках: ``{patientName}``, ``{time}``,
   ``{amount}``, ``{count}``... Подставляются через ``str.format(**params)`` в
   :func:`render`. Имена плейсхолдеров держим одинаковыми между языками одного
   типа (иначе рендер молча оставит фигурные скобки как есть — см. ниже).
3. ``sync.day`` в словаре НЕТ и не будет: это тихий data-only пуш
   (``content-available``), видимого текста у него никогда не бывает.
4. Никаких медданных в тексте — только имя пациента, дата/время, сумма,
   количество (см. ADR §2 «Никаких медданных»). Диагнозы, жалобы — нельзя.

⚠️ Значения ``kg`` — базовые черновики (не носитель языка), требуют вычитки
перед релизом; тот же статус, что у ``app/shared/i18n/messages.py``. ``kz`` и
``en`` пока не заведены — :func:`render` в их отсутствие сам откатится на RU.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.shared.db.enums import DEFAULT_LANG, Lang

logger = structlog.get_logger(__name__)

#: Пустой текст — последний рубеж фолбэка: сам тип известен, а перевода нет ни
#: на запрошенный язык, ни на RU (в норме недостижимо — каждый тип ниже заведён
#: минимум с RU). Пустой ``title`` приложение воспринимает как сигнал взять
#: собственный текст (``texts.ts``), поэтому это безопасный дефолт, а не баг.
_EMPTY_TEXT: dict[str, str] = {"title": "", "body": "", "speak": ""}

#: Все типы пуш-событий каталога (см. ADR §4), включая ``sync.day`` — он тут
#: только для полноты списка «что вообще шлём», текста у него нет и не будет.
PUSH_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "sync.day",
        "appointment.soon",
        "appointment.arrival",
        "appointment.moved",
        "appointment.canceled",
        "appointment.created",
        "day.morning",
        "day.evening",
        "debt.created",
        "debt.settled",
        "cash.dayClosed",
        "subscription.expiring",
        "subscription.expired",
        "subscription.paid",
        "staff.joined",
        "staff.removed",
        "role.changed",
    }
)

PUSH_TEXTS: dict[str, dict[Lang, dict[str, str]]] = {
    # ── Приёмы ───────────────────────────────────────────────────────────────
    "appointment.soon": {
        Lang.RU: {
            "title": "Скоро приём",
            "body": "{patientName} — через 15 минут, в {time}",
            "speak": "Через пятнадцать минут приём пациента {patientName}",
        },
        Lang.KG: {
            "title": "Кабыл алуу жакындады",
            "body": "{patientName} — 15 мүнөттөн кийин, саат {time}",
            "speak": "15 мүнөттөн кийин {patientName} пациентти кабыл аласыз",
        },
    },
    "appointment.arrival": {
        Lang.RU: {
            "title": "Приём должен был начаться",
            "body": "{patientName} — назначено на {time}",
            "speak": "Приём пациента {patientName}, назначенный на {time}, должен был начаться",
        },
        Lang.KG: {
            "title": "Кабыл алуу башталышы керек эле",
            "body": "{patientName} — {time}га дайындалган",
            "speak": "{patientName} пациенттин кабылдоосу {time} саатта башталышы керек эле",
        },
    },
    "appointment.moved": {
        Lang.RU: {
            "title": "Приём перенесён",
            "body": "{patientName} — новое время {time}",
            "speak": "Приём пациента {patientName} перенесён на {time}",
        },
        Lang.KG: {
            "title": "Кабыл алуу жылдырылды",
            "body": "{patientName} — жаңы убакыт {time}",
            "speak": "{patientName} пациенттин кабылдоосу {time} убактысына жылдырылды",
        },
    },
    "appointment.canceled": {
        Lang.RU: {
            "title": "Приём отменён",
            "body": "{patientName} — {time}",
            "speak": "Приём пациента {patientName}, назначенный на {time}, отменён",
        },
        Lang.KG: {
            "title": "Кабыл алуу жокко чыгарылды",
            "body": "{patientName} — {time}",
            "speak": "{patientName} пациенттин {time} убактысындагы кабылдоосу жокко чыгарылды",
        },
    },
    "appointment.created": {
        Lang.RU: {
            "title": "Новая запись",
            "body": "{patientName} записан(а) к вам на {time}",
            "speak": "Вас записали на приём: {patientName}, {time}",
        },
        Lang.KG: {
            "title": "Жаңы жазуу",
            "body": "{patientName} сизге {time} убактысына жазылды",
            "speak": "Сизди кабыл алууга жазышты: {patientName}, {time}",
        },
    },
    # ── Сводки дня ───────────────────────────────────────────────────────────
    "day.morning": {
        Lang.RU: {
            "title": "Ваш день",
            "body": "Сегодня {count} приёмов, первый в {time}",
            "speak": "Сегодня у вас {count} приёмов, первый начинается в {time}",
        },
        Lang.KG: {
            "title": "Бүгүнкү күнүңүз",
            "body": "Бүгүн {count} кабылдоо, биринчиси саат {time}",
            "speak": "Бүгүн сизде {count} кабылдоо, биринчиси саат {time} башталат",
        },
    },
    "day.evening": {
        Lang.RU: {
            "title": "Итоги дня",
            "body": "Завершено приёмов: {count}",
            "speak": "Сегодня вы завершили {count} приёмов",
        },
        Lang.KG: {
            "title": "Күндүн жыйынтыгы",
            "body": "Аяктаган кабылдоолор: {count}",
            "speak": "Бүгүн сиз {count} кабылдоону аяктадыңыз",
        },
    },
    # ── Деньги ───────────────────────────────────────────────────────────────
    "debt.created": {
        Lang.RU: {
            "title": "Новый долг",
            "body": "{patientName} — {amount} сом",
            "speak": "У пациента {patientName} образовался долг {amount} сом",
        },
        Lang.KG: {
            "title": "Жаңы карыз",
            "body": "{patientName} — {amount} сом",
            "speak": "{patientName} пациенттин {amount} сом карызы пайда болду",
        },
    },
    "debt.settled": {
        Lang.RU: {
            "title": "Долг закрыт",
            "body": "{patientName} — оплатил(а) {amount} сом",
            "speak": "Пациент {patientName} закрыл долг на {amount} сом",
        },
        Lang.KG: {
            "title": "Карыз жабылды",
            "body": "{patientName} — {amount} сом төлөдү",
            "speak": "{patientName} пациент {amount} сом карызын жапты",
        },
    },
    "cash.dayClosed": {
        Lang.RU: {
            "title": "Касса за день",
            "body": "Выручка: {amount} сом",
            "speak": "Выручка клиники за сегодня — {amount} сом",
        },
        Lang.KG: {
            "title": "Күндүн кассасы",
            "body": "Түшкөн акча: {amount} сом",
            "speak": "Бүгүнкү клиниканын түшүмү — {amount} сом",
        },
    },
    # ── Подписка ─────────────────────────────────────────────────────────────
    "subscription.expiring": {
        Lang.RU: {
            "title": "Подписка скоро закончится",
            "body": "Осталось {count} дн. — продлите доступ",
            "speak": "Подписка Скрезо заканчивается через {count} дней, пора продлить",
        },
        Lang.KG: {
            "title": "Жазылуу мөөнөтү бүтүп жатат",
            "body": "{count} күн калды — узартыңыз",
            "speak": "Скрезо жазылуусу {count} күндөн кийин бүтөт, узартуу керек",
        },
    },
    "subscription.expired": {
        Lang.RU: {
            "title": "Подписка закончилась",
            "body": "Доступ ограничен — продлите подписку",
            "speak": "Подписка Скрезо закончилась, доступ к приложению ограничен",
        },
        Lang.KG: {
            "title": "Жазылуу мөөнөтү бүттү",
            "body": "Мүмкүнчүлүк чектелди — жазылууну узартыңыз",
            "speak": "Скрезо жазылуусу бүттү, колдонмого мүмкүнчүлүк чектелди",
        },
    },
    "subscription.paid": {
        Lang.RU: {
            "title": "Оплата получена",
            "body": "Подписка продлена до {date}",
            "speak": "Оплата получена, подписка Скрезо продлена",
        },
        Lang.KG: {
            "title": "Төлөм кабыл алынды",
            "body": "Жазылуу {date} чейин узартылды",
            "speak": "Төлөм кабыл алынды, Скрезо жазылуусу узартылды",
        },
    },
    # ── Команда ──────────────────────────────────────────────────────────────
    "staff.joined": {
        Lang.RU: {
            "title": "Новый сотрудник",
            "body": "{name} присоединил(ся/ась) к клинике",
            "speak": "В клинику добавлен новый сотрудник — {name}",
        },
        Lang.KG: {
            "title": "Жаңы кызматкер",
            "body": "{name} клиникага кошулду",
            "speak": "Клиникага жаңы кызматкер кошулду — {name}",
        },
    },
    "staff.removed": {
        Lang.RU: {
            "title": "Сотрудник удалён",
            "body": "{name} больше не в команде",
            "speak": "Сотрудник {name} удалён из клиники",
        },
        Lang.KG: {
            "title": "Кызматкер өчүрүлдү",
            "body": "{name} эми командада эмес",
            "speak": "{name} кызматкери клиникадан өчүрүлдү",
        },
    },
    "role.changed": {
        Lang.RU: {
            "title": "Роль изменена",
            "body": "{name} — новая роль: {roleName}",
            "speak": "У сотрудника {name} изменена роль на {roleName}",
        },
        Lang.KG: {
            "title": "Роль өзгөртүлдү",
            "body": "{name} — жаңы ролу: {roleName}",
            "speak": "{name} кызматкеринин ролу {roleName} болуп өзгөртүлдү",
        },
    },
}


def _safe_format(template: str, params: dict[str, Any]) -> str:
    """Подставить плейсхолдеры, не роняя рендер на забытом параметре.

    Текст пуша — не то, из-за чего должен упасть use-case (запись пациента,
    закрытие приёма и т.п.): при нехватке параметра строка остаётся с
    ``{плейсхолдером}`` как есть, а несовпадение пишется в лог — это ошибка
    конфига (забыли передать параметр или переименовали плейсхолдер), а не
    клиента.
    """
    try:
        return template.format(**params)
    except KeyError, IndexError:
        logger.warning("push_text_placeholder_missing", template=template, params=sorted(params))
        return template


def render(event_type: str, lang: Lang, **params: Any) -> dict[str, str] | None:
    """Собрать ``title``/``body``/``speak`` пуша по типу и языку устройства.

    :param event_type: тип пуша из каталога (``PUSH_EVENT_TYPES``), напр.
        ``"appointment.soon"``.
    :param lang: язык устройства-получателя (``Device.locale``).
    :param params: значения плейсхолдеров (``patientName``, ``time``, ...).
    :return: словарь из трёх строк, либо ``None`` — у типа нет текста вообще
        (``sync.day`` и любой другой data-only/неизвестный тип): тогда пуш
        уходит без ``notification``, только с ``data``.
    """
    entry = PUSH_TEXTS.get(event_type)
    if entry is None:
        return None
    texts = entry.get(lang) or entry.get(DEFAULT_LANG) or _EMPTY_TEXT
    return {key: _safe_format(value, params) for key, value in texts.items()}
