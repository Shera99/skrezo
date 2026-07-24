"""Заявки клиник: второй путь регистрации («клиника с сотрудниками»).

Добавляет:

* enum-типы ``doctors_count_range`` (2-5 / 6-10 / 10+) и
  ``clinic_application_status`` (pending / reviewed / rejected);
* таблицу ``clinic_application`` — заявка после подтверждённого OTP: название
  компании, город, размер, телефон + статус рассмотрения и кто его поставил.

Клиника по заявке автоматически НЕ создаётся: Skrezo рассматривает её в админке
и заводит клиента вручную.

Revision ID: 0023_clinic_application
Revises: 0022_permission_in_trial
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_clinic_application"
down_revision: str | None = "0022_permission_in_trial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCTORS_RANGE = sa.Enum("2-5", "6-10", "10+", name="doctors_count_range")
_APPLICATION_STATUS = sa.Enum(
    "pending", "reviewed", "rejected", name="clinic_application_status"
)


def upgrade() -> None:
    op.create_table(
        "clinic_application",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column(
            "city_id", sa.Uuid(), sa.ForeignKey("city.id"), nullable=False
        ),
        sa.Column("doctors_count", _DOCTORS_RANGE, nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("status", _APPLICATION_STATUS, nullable=False, server_default="pending"),
        sa.Column(
            "reviewed_by_account_id", sa.Uuid(),
            sa.ForeignKey("account.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_clinic_application_phone", "clinic_application", ["phone"])
    op.create_index(
        "ix_clinic_application_status_created", "clinic_application", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_clinic_application_status_created", table_name="clinic_application")
    op.drop_index("ix_clinic_application_phone", table_name="clinic_application")
    op.drop_table("clinic_application")
    bind = op.get_bind()
    _APPLICATION_STATUS.drop(bind, checkfirst=True)
    _DOCTORS_RANGE.drop(bind, checkfirst=True)
