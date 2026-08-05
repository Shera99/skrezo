"""Приём = список позиций: количество, позиция без зуба, несколько зубов на позицию.

Реальный приём почти всегда многопозиционный («кариес 36 + чистка»), а прежняя
``appointment_tooth`` умела ровно одно: одна манипуляция — один зуб. Из-за этого
чек занижался (нет количества), а чистку или консультацию к чеку было не
привязать вовсе.

Что делает миграция:

* ``appointment_item`` — строка чека: услуга, **цена за единицу на момент
  оказания**, ``quantity`` (CHECK > 0), статус, комментарий;
* ``appointment_item_tooth`` — связка «позиция ↔ зуб» многие-ко-многим: позиция
  может покрывать несколько зубов (мост на 35–37) или ни одного (чистка). Через
  неё же идёт сквозная история зуба;
* переносит существующие манипуляции один-в-один (``quantity = 1``, один зуб на
  позицию) и удаляет ``appointment_tooth``;
* переименовывает enum ``appointment_tooth_status`` → ``appointment_item_status``:
  статус теперь у позиции, а не у зуба.

⚠️ **downgrade теряет данные** (обратной дороги без потерь нет, как у 0026):
позиции без зубов исчезают — в старой модели их нельзя выразить; количество
пропадает, и приём с «снимок ×3» подешевеет до одного. Позиция на несколько
зубов, наоборот, разворачивается обратно в строку на каждый зуб — это ровно
старая модель. Итоги приёмов (``price_total_tiyin``) при этом НЕ пересчитываются:
их считает прикладной слой, и после downgrade они останутся «новыми».

Revision ID: 0029_appointment_items
Revises: 0028_refunds_index
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_appointment_items"
down_revision: str | None = "0028_refunds_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: тип уже есть в БД (переименован из appointment_tooth_status),
# и CREATE TABLE не должен пытаться создать его заново — как и удалять на откате.
_STATUS_ENUM = postgresql.ENUM(
    "planned",
    "started",
    "completed",
    "cancelled",
    name="appointment_item_status",
    create_type=False,
)


def upgrade() -> None:
    # Тип тот же — меняется только имя: статус переехал с зуба на позицию.
    op.execute("ALTER TYPE appointment_tooth_status RENAME TO appointment_item_status")

    op.create_table(
        "appointment_item",
        sa.Column(
            "id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "appointment_id",
            sa.Uuid(),
            sa.ForeignKey("appointment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id", sa.Uuid(), sa.ForeignKey("service.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "doctor_membership_id",
            sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status", _STATUS_ENUM, nullable=False, server_default=sa.text("'planned'")
        ),
        sa.Column("price_tiyin", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_appointment_item_quantity_positive"),
    )
    op.create_index("ix_appointment_item_appointment", "appointment_item", ["appointment_id"])

    op.create_table(
        "appointment_item_tooth",
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("appointment_item.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "patient_tooth_id",
            sa.Uuid(),
            sa.ForeignKey("patient_tooth.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_appointment_item_tooth_patient_tooth",
        "appointment_item_tooth",
        ["patient_tooth_id"],
    )

    # Перенос: манипуляция по зубу = позиция на один зуб в количестве 1. Id
    # сохраняются — на них ссылались выдачи истории и уже показанные клиенту карточки.
    op.execute(
        """
        INSERT INTO appointment_item (
            id, appointment_id, service_id, doctor_membership_id,
            status, price_tiyin, quantity, comment, created_at
        )
        SELECT id, appointment_id, service_id, doctor_membership_id,
               status, price_tiyin, 1, comment, created_at
        FROM appointment_tooth
        """
    )
    op.execute(
        """
        INSERT INTO appointment_item_tooth (item_id, patient_tooth_id)
        SELECT id, patient_tooth_id FROM appointment_tooth
        """
    )
    op.drop_table("appointment_tooth")


def downgrade() -> None:
    op.create_table(
        "appointment_tooth",
        sa.Column(
            "id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "appointment_id",
            sa.Uuid(),
            sa.ForeignKey("appointment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_tooth_id",
            sa.Uuid(),
            sa.ForeignKey("patient_tooth.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id", sa.Uuid(), sa.ForeignKey("service.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "doctor_membership_id",
            sa.Uuid(),
            sa.ForeignKey("membership.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status", _STATUS_ENUM, nullable=False, server_default=sa.text("'planned'")
        ),
        sa.Column("price_tiyin", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_appointment_tooth_appointment", "appointment_tooth", ["appointment_id"])
    op.create_index(
        "ix_appointment_tooth_patient_tooth", "appointment_tooth", ["patient_tooth_id"]
    )

    # Строка на каждый зуб позиции — это и есть старая модель. Позиции без зубов
    # (чистка, консультация) в неё не помещаются и теряются; количество тоже.
    op.execute(
        """
        INSERT INTO appointment_tooth (
            id, appointment_id, patient_tooth_id, service_id, doctor_membership_id,
            status, price_tiyin, comment, created_at
        )
        SELECT gen_random_uuid(), i.appointment_id, t.patient_tooth_id, i.service_id,
               i.doctor_membership_id, i.status, i.price_tiyin, i.comment, i.created_at
        FROM appointment_item i
        JOIN appointment_item_tooth t ON t.item_id = i.id
        """
    )

    op.drop_index("ix_appointment_item_tooth_patient_tooth", table_name="appointment_item_tooth")
    op.drop_table("appointment_item_tooth")
    op.drop_index("ix_appointment_item_appointment", table_name="appointment_item")
    op.drop_table("appointment_item")
    op.execute("ALTER TYPE appointment_item_status RENAME TO appointment_tooth_status")
