"""Откуда брать значение переменной шаблона — чтобы сотрудник не вводил его руками.

Meta не подставляет значения ``{{1}}``, ``{{2}}`` сама: их передаёт отправитель.
И Meta, и синхронизация знают о переменной только её номер, поэтому смысл
переменной («имя пациента», «дата записи») задаёт человек: Skrezo — у общих
шаблонов платформы, клиника — у своих (решение 19.10.2026). По источнику
бэк заранее заполняет значения для конкретного диалога, а сотрудник лишь
проверяет их перед отправкой.

``manual`` — значение вводится вручную (как раньше), это же — значение по
умолчанию у каждой переменной.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence

from app.shared.db.enums import Lang


class VariableSource(enum.StrEnum):
    """Источник значения переменной шаблона."""

    MANUAL = "manual"
    PATIENT_NAME = "patient_name"
    PATIENT_FULL_NAME = "patient_full_name"
    CLINIC_NAME = "clinic_name"
    CLINIC_PHONE = "clinic_phone"
    CLINIC_ADDRESS = "clinic_address"
    DOCTOR_NAME = "doctor_name"
    APPOINTMENT_DATE = "appointment_date"
    APPOINTMENT_TIME = "appointment_time"
    DEBT_AMOUNT = "debt_amount"


#: Подпись источника для выбора в кабинете и для поля переменной без своей подписи.
_LABELS: dict[VariableSource, dict[Lang, str]] = {
    VariableSource.MANUAL: {Lang.RU: "Вручную", Lang.KG: "Колго жазуу"},
    VariableSource.PATIENT_NAME: {Lang.RU: "Имя пациента", Lang.KG: "Пациенттин аты"},
    VariableSource.PATIENT_FULL_NAME: {
        Lang.RU: "Имя и фамилия пациента",
        Lang.KG: "Пациенттин аты-жөнү",
    },
    VariableSource.CLINIC_NAME: {Lang.RU: "Название клиники", Lang.KG: "Клиниканын аталышы"},
    VariableSource.CLINIC_PHONE: {Lang.RU: "Телефон клиники", Lang.KG: "Клиниканын телефону"},
    VariableSource.CLINIC_ADDRESS: {Lang.RU: "Адрес клиники", Lang.KG: "Клиниканын дареги"},
    VariableSource.DOCTOR_NAME: {
        Lang.RU: "Врач ближайшей записи",
        Lang.KG: "Жакынкы жазылуунун дарыгери",
    },
    VariableSource.APPOINTMENT_DATE: {
        Lang.RU: "Дата ближайшей записи",
        Lang.KG: "Жакынкы жазылуунун күнү",
    },
    VariableSource.APPOINTMENT_TIME: {
        Lang.RU: "Время ближайшей записи",
        Lang.KG: "Жакынкы жазылуунун убактысы",
    },
    VariableSource.DEBT_AMOUNT: {Lang.RU: "Долг пациента, сом", Lang.KG: "Пациенттин карызы, сом"},
}


def source_label(source: VariableSource, lang: Lang) -> str:
    """Подпись источника на языке запроса; казахского и английского нет — русская."""
    labels = _LABELS[source]
    return labels.get(lang) or labels[Lang.RU]


def parse_source(raw: object) -> VariableSource:
    """Значение из базы или запроса → источник; незнакомое — «вручную»."""
    try:
        return VariableSource(str(raw))
    except ValueError:
        return VariableSource.MANUAL


def normalize_sources(
    variables: Sequence[object], sources: Sequence[object]
) -> list[VariableSource]:
    """Источники по порядку переменных: ровно столько же, недостающие — «вручную».

    Синхронизация могла принести новую версию шаблона с другим числом
    переменных — лишние источники отбрасываются, как и лишние подписи.
    """
    return [
        parse_source(sources[i]) if i < len(sources) else VariableSource.MANUAL
        for i in range(len(variables))
    ]
