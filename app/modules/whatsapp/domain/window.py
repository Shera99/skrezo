"""24-часовое окно WhatsApp — чистая арифметика, без БД.

Правило Meta: свободный текст можно отправлять только сутки после последнего
сообщения ПАЦИЕНТА. Дальше — только одобренные шаблоны. Окно считается от
``last_patient_msg_at`` и только от него: собственные ответы клиники окно не
продлевают (иначе оно «открыто» вечно, а Graph API отвечает отказом).
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: Длительность окна свободного текста после сообщения пациента.
WINDOW = timedelta(hours=24)


def is_window_open(last_patient_msg_at: datetime | None, now: datetime) -> bool:
    """Открыто ли окно свободного текста.

    ``None`` — пациент не писал вовсе (диалог начала клиника): окна нет,
    доступны только шаблоны.
    """
    if last_patient_msg_at is None:
        return False
    return now - last_patient_msg_at < WINDOW
