"""Разбор ошибки EXCLUDE-констрейнта при записи на приём.

Прикладная проверка (``AvailabilityService.ensure_bookable``) отсеивает занятые
слоты, но между проверкой и вставкой есть окно гонки: два параллельных запроса
успевают увидеть слот пустым и оба идут писать. Второго ловит констрейнт
``ex_appointment_doctor_overlap`` — и это штатный конфликт (409), а не 500.

Важно, ГДЕ ловить. ``ExclusionViolationError`` поднимается не из ``commit()``, а
из первого же ``flush()``, отправляющего INSERT/UPDATE в БД, — а флашит
репозиторий внутри себя. Поэтому перехват обязан накрывать всю работу с сессией,
а не только коммит: отсюда контекстный менеджер, а не обёртка вокруг ``commit()``.
После ошибки транзакция в БД уже сломана, и сессию нужно откатить, иначе
следующая операция на этом соединении получит ``DBAPIError``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError

_OVERLAP_CONSTRAINT = "ex_appointment_doctor_overlap"


@asynccontextmanager
async def booking_guard(session: AsyncSession) -> AsyncIterator[None]:
    """Накрыть запись приёма: пересечение интервалов у врача → 409 вместо 500.

    Оборачивать нужно и саму запись, и коммит::

        async with booking_guard(session):
            appointment = await repository.create(...)
            await session.commit()
    """
    try:
        yield
    except IntegrityError as exc:
        # Транзакция уже в failed-состоянии: без отката соединение «отравлено».
        await session.rollback()
        if _OVERLAP_CONSTRAINT in str(exc.orig):
            raise ConflictError("У врача уже есть приём в это время", code="slot_taken") from exc
        raise
