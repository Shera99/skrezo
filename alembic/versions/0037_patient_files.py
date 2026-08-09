"""Файлы пациента: снимки и документы.

Сам файл лежит на диске сервера (``STORAGE_ROOT``), в БД — только метаданные и
принадлежность. Класть мегабайтные снимки в таблицу значит превратить бэкап и
репликацию в отдельную задачу, а отдавать их всё равно нужно через ручку с
проверкой прав.

Файл привязан к ПАЦИЕНТУ, а он глобальный: снимок виден всем клиникам, которые
лечат этого человека, — как и сквозная история зуба. ``clinic_id`` хранится
рядом, чтобы было видно, кто загрузил.

Удаление мягкое (``deleted_at``): снимок — клинические данные, и «случайно
удалил» не должно означать «потеряли навсегда».

Revision ID: 0037_patient_files
Revises: 0036_clinic_audit
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_patient_files"
down_revision: str | None = "0036_clinic_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patient_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("clinic_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Enum("images", "files", name="file_kind"), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("uploaded_by_membership_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointment.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["uploaded_by_membership_id"], ["membership.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Карта пациента открывается постоянно и просит свежие файлы сверху.
    op.create_index(
        "ix_patient_file_patient_created",
        "patient_file",
        ["patient_id", sa.literal_column("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_patient_file_patient_created", table_name="patient_file")
    op.drop_table("patient_file")
    # Тип enum создаётся вместе с таблицей, но не удаляется вместе с ней.
    sa.Enum(name="file_kind").drop(op.get_bind(), checkfirst=True)
