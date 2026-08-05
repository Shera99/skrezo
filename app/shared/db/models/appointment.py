"""Таблица приёмов — связывает клинику, врача и пациента.

Врач = наш ``membership`` (account+роль), а не отдельная сущность. Итоговая
стоимость приёма — ``price_total_tiyin`` (тыйыны); манипуляции по зубам — в ``appointment_tooth``.

Приём занимает интервал ``[scheduled_at, scheduled_at + duration_min)``. Пересечение
интервалов у одного врача запрещено EXCLUDE-констрейнтом
``ex_appointment_doctor_overlap`` (см. миграцию 0020) — он же страхует от гонки,
когда два запроса одновременно занимают один слот.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import (
    AppointmentStatus,
    AppointmentStatusType,
    AppointmentType,
    AppointmentTypeType,
    CancelReason,
    CancelReasonType,
)


class Appointment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "appointment"
    __table_args__ = (
        # Под выборку «приёмы клиники за день» («лента дня»).
        Index("ix_appointment_clinic_scheduled_at", "clinic_id", "scheduled_at"),
        CheckConstraint(
            "discount_percent BETWEEN 0 AND 100", name="ck_appointment_discount_percent"
        ),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Врач — членство в клинике (наш account+роль); необязательно.
    doctor_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="SET NULL"), nullable=True
    )
    # Основная услуга приёма: из неё берётся длительность по умолчанию.
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("service.id", ondelete="SET NULL"), nullable=True
    )
    appointment_type: Mapped[AppointmentType] = mapped_column(
        AppointmentTypeType, nullable=False, default=AppointmentType.TREATMENT
    )
    # NULL допустим ТОЛЬКО у черновика (``status = draft``) — это стережёт
    # CHECK ``ck_appointment_scheduled_at_required``. Для остальных статусов дата
    # обязательна: без неё приём выпадает из ленты дня, а tstzrange(NULL, …) даёт
    # безграничный диапазон, пересекающийся со всеми приёмами врача.
    # Без server_default: SQLAlchemy трактует None как «значение не задано» и
    # подставил бы now(), из-за чего черновик получал бы дату «сейчас». Дату для
    # обычной записи проставляет прикладной слой.
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Длительность приёма в минутах — высота карточки в «ленте дня».
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("30"))
    status: Mapped[AppointmentStatus] = mapped_column(
        AppointmentStatusType, nullable=False, default=AppointmentStatus.SCHEDULED
    )
    # Итог К ОПЛАТЕ в тыйынах (1/100 сома): сумма позиций МИНУС скидка. Именно от
    # него считается долг приёма, поэтому семантика «сколько должны» не меняется.
    price_total_tiyin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Скидка процентом — так её ограничивает потолок роли (``role.max_discount_percent``).
    # Сумма скидки хранится рядом: её показывает чек и по ней считается, сколько
    # клиника раздала за период. Обе величины пересчитываются при правке позиций.
    discount_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    discount_tiyin: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Причина отмены — справочником (иначе отмены нечем считать); комментарий
    # обязателен при ``other``. Заполняются вместе со статусом ``cancelled`` и
    # очищаются при снятии отмены, чтобы у активного приёма не висел старый повод.
    cancel_reason: Mapped[CancelReason | None] = mapped_column(CancelReasonType, nullable=True)
    cancel_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Приём без позиций закрыть нельзя — кроме явного «бесплатно» с пояснением
    # (``ck_appointment_no_charge_comment``). Бесплатный приём бывает, но должен
    # быть решением, а не следствием незаполненной формы: иначе нулевые чеки
    # копятся и портят среднюю выручку.
    no_charge: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    no_charge_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
