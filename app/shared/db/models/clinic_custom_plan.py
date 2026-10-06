"""Спецплан клиники — индивидуальные условия, которые ставит команда Skrezo.

Публичные тарифы — пакеты по числу мест («Старт», «Практика», «Клиника»), и
между ними клиника переходит сама. Клинике, которой нужно больше мест или
другая цена, Skrezo договаривается отдельно и ставит спецплан: своё число мест
и своя цена в месяц. Он виден только этой клинике и, пока он есть, заменяет
цену пакетов везде — в оплате, списании с баланса и автопродлении; переход
между пакетами в кабинете при этом закрыт. Строка одна на клинику.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class ClinicCustomPlan(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clinic_custom_plan"
    __table_args__ = (
        CheckConstraint("seats >= 1", name="ck_clinic_custom_plan_seats"),
        CheckConstraint("price_tiyin > 0", name="ck_clinic_custom_plan_price"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    #: Как план называется у клиники: «Клиника+ 14 мест».
    title: Mapped[str] = mapped_column(Text, nullable=False)
    #: Мест по плану — он же лимит сотрудников клиники (``clinic.seats_limit``).
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Цена в месяц, тыйыны.
    price_tiyin: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Внутренняя заметка Skrezo: о чём договорились.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    set_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
