"""Смена тарифа клиникой и спецплан от Skrezo.

* ``clinic_custom_plan`` — индивидуальные условия клиники (мест и цена в месяц),
  ставит команда Skrezo; одна строка на клинику;
* ``plan_request`` — заявка клиники на условия сверх публичных тарифов; открытая
  заявка у клиники одна (частичный уникальный индекс).

Переход между публичными пакетами новых таблиц не требует: пакет клиники — это
её лимит мест (``clinic.seats_limit``), доплата за остаток периода — строка
журнала с источником ``plan_change``.

Revision ID: 0069_subscription_plans
Revises: 0068_whatsapp_self_service
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0069_subscription_plans"
down_revision = "0068_whatsapp_self_service"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[datetime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "clinic_custom_plan",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "clinic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("price_tiyin", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "set_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
        *_timestamps(),
        sa.CheckConstraint("seats >= 1", name="ck_clinic_custom_plan_seats"),
        sa.CheckConstraint("price_tiyin > 0", name="ck_clinic_custom_plan_price"),
    )

    op.create_table(
        "plan_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "clinic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinic.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "resolved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("account.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_timestamps(),
        sa.CheckConstraint("seats >= 1", name="ck_plan_request_seats"),
        sa.CheckConstraint("status IN ('new', 'done', 'declined')", name="ck_plan_request_status"),
    )
    op.create_index(
        "uq_plan_request_open",
        "plan_request",
        ["clinic_id"],
        unique=True,
        postgresql_where=sa.text("status = 'new'"),
    )
    op.create_index("ix_plan_request_status_created", "plan_request", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_plan_request_status_created", table_name="plan_request")
    op.drop_index("uq_plan_request_open", table_name="plan_request")
    op.drop_table("plan_request")
    op.drop_table("clinic_custom_plan")
