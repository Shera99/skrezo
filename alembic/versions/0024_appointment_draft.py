"""Черновик приёма: пациент заведён в клинику, но дата и услуга ещё не выбраны.

Зачем: клиника видит пациента только через наличие приёма (список пациентов —
это ``patient JOIN appointment``). Чтобы завести человека «на будущее», нужен
приём без даты; позже он доводится до полноценной записи.

Что делает миграция:

* добавляет значение ``draft`` в enum ``appointment_status``;
* снимает NOT NULL с ``appointment.scheduled_at`` — но **только** под охраной
  CHECK ``ck_appointment_scheduled_at_required``: пустая дата легальна
  исключительно у черновика. Миграция 0020 делала колонку NOT NULL именно
  потому, что NULL ломал ленту дня и EXCLUDE-констрейнт; этот CHECK сохраняет
  ту же гарантию для всех рабочих статусов;
* пересобирает ``ex_appointment_doctor_overlap``, исключив из него черновики:
  ``appointment_tstzrange(NULL, …)`` — безграничный диапазон, он пересекался бы
  со всеми приёмами врача и блокировал любую запись.

Revision ID: 0024_appointment_draft
Revises: 0023_clinic_application
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_appointment_draft"
down_revision: str | None = "0023_clinic_application"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OVERLAP_CONSTRAINT = "ex_appointment_doctor_overlap"
_INTERVAL_FN = "appointment_tstzrange"
_SCHEDULED_AT_CHECK = "ck_appointment_scheduled_at_required"

# Пересборка EXCLUDE: тот же констрейнт, что в 0020, плюс отсечение черновиков.
_DROP_OVERLAP = f"ALTER TABLE appointment DROP CONSTRAINT IF EXISTS {_OVERLAP_CONSTRAINT}"
_CREATE_OVERLAP = """
    ALTER TABLE appointment
    ADD CONSTRAINT {name}
    EXCLUDE USING gist (
        doctor_membership_id WITH =,
        {fn}(scheduled_at, duration_min) WITH &&
    )
    WHERE (
        doctor_membership_id IS NOT NULL
        AND status <> 'cancelled'::appointment_status
        {extra}
    )
"""


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE нельзя использовать в той же транзакции, где
    # значение создано, а alembic по умолчанию гонит весь upgrade одной
    # транзакцией — поэтому отдельный autocommit-блок.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE appointment_status "
            "ADD VALUE IF NOT EXISTS 'draft' BEFORE 'scheduled'"
        )

    # Снимаем и NOT NULL, и server_default: с дефолтом now() SQLAlchemy трактует
    # None как «не задано» и проставил бы черновику текущее время вместо пустой даты.
    op.alter_column(
        "appointment",
        "scheduled_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        server_default=None,
    )
    op.create_check_constraint(
        _SCHEDULED_AT_CHECK,
        "appointment",
        "scheduled_at IS NOT NULL OR status = 'draft'::appointment_status",
    )

    op.execute(_DROP_OVERLAP)
    op.execute(
        _CREATE_OVERLAP.format(
            name=_OVERLAP_CONSTRAINT,
            fn=_INTERVAL_FN,
            extra="AND status <> 'draft'::appointment_status",
        )
    )


def downgrade() -> None:
    # Черновики не переживут возврат NOT NULL — их дата пуста по определению.
    op.execute("DELETE FROM appointment WHERE status = 'draft'::appointment_status")

    op.execute(_DROP_OVERLAP)
    op.execute(_CREATE_OVERLAP.format(name=_OVERLAP_CONSTRAINT, fn=_INTERVAL_FN, extra=""))

    op.drop_constraint(_SCHEDULED_AT_CHECK, "appointment", type_="check")
    op.alter_column(
        "appointment",
        "scheduled_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    # Значение 'draft' в enum остаётся: ALTER TYPE ... DROP VALUE в Postgres нет.
