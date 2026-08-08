"""Позиция приёма — строка чека: услуга, цена за единицу, количество.

Реальный приём почти всегда многопозиционный («кариес 36 + чистка»), поэтому
приём — это **список позиций**, а не одна услуга. Отсюда три следствия, которых
не было у прежней таблицы «манипуляция по зубу»:

* **количество** (``quantity``) — «снимок ×3» одной строкой, а не тремя;
* **позиция без зуба** — чистка, консультация, анестезия к зубу не привязаны;
* **несколько зубов на позицию** — мост на 35–37 это одна работа с одной ценой
  (связка ``appointment_item_tooth``).

``price_tiyin`` — цена **за единицу на момент оказания**: прайс потом меняется,
а чек прошедшего приёма меняться не должен. Итог позиции = ``price_tiyin ×
quantity``, итог приёма (``appointment.price_total_tiyin``) — сумма позиций.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import AppointmentItemStatus, AppointmentItemStatusType


class AppointmentItem(Base, UUIDPKMixin):
    __tablename__ = "appointment_item"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_appointment_item_quantity_positive"),
        # Позиции приёма читаются пачкой: карточка приёма, итог, отчёт услуг.
        Index("ix_appointment_item_appointment", "appointment_id"),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointment.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("service.id", ondelete="SET NULL"), nullable=True
    )
    doctor_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[AppointmentItemStatus] = mapped_column(
        AppointmentItemStatusType, nullable=False, default=AppointmentItemStatus.PLANNED
    )
    # Цена за ЕДИНИЦУ на момент оказания (тыйыны), а не итог строки.
    price_tiyin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Порядок позиции в чеке (с нуля). Нужен именно колонкой: ``created_at`` у всех
    # строк одного приёма одинаков (время транзакции), и без него порядок выдачи
    # и ответ на вопрос «какая работа по зубу последняя» зависели бы от плана запроса.
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
