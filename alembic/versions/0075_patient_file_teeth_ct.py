"""Файлы пациента: привязка к зубам, дата снимка и КТ (DICOM).

Запрос клиник (20.10.2026): к карте пациента прикладывают общий снимок
(например, КТ всей челюсти), а к зубам — свои фото и снимки. Файл остаётся в
той же таблице: ``tooth_numbers`` пустой — общий файл пациента, непустой —
файл этих зубов. Список, а не один номер: прицельный снимок или фрагмент КТ
обычно захватывает соседние зубы (36 и 37).

``taken_date`` — дата самого снимка. Она не совпадает с датой загрузки, когда
пациент приносит КТ годичной давности, а «самые свежие снимки» сортируются
именно по ней. Пусто — дата неизвестна, считаем датой загрузки.

Новый вид ``ct`` — компьютерная томография: одиночный DICOM-файл или ZIP-архив
исследования. Исследование весит сотни мегабайт, поэтому ``size_bytes``
расширяется до BIGINT: потолок INTEGER (2 ГиБ) иначе упирается в лимит,
поднятый настройкой.

Revision ID: 0075_patient_file_teeth_ct
Revises: 0074_template_var_sources
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0075_patient_file_teeth_ct"
down_revision = "0074_template_var_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE — вне общей транзакции alembic (см. 0025).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE file_kind ADD VALUE IF NOT EXISTS 'ct'")

    op.add_column(
        "patient_file",
        sa.Column(
            "tooth_numbers",
            postgresql.ARRAY(sa.SmallInteger()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column("patient_file", sa.Column("taken_date", sa.Date(), nullable=True))
    op.alter_column(
        "patient_file",
        "size_bytes",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Файлы больше 2 ГиБ в INTEGER не влезут — откат на них упадёт, и это
    # правильно: молча обрезать размер нельзя.
    op.alter_column(
        "patient_file",
        "size_bytes",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
        existing_nullable=False,
    )
    op.drop_column("patient_file", "taken_date")
    op.drop_column("patient_file", "tooth_numbers")
    # Значение 'ct' из enum PostgreSQL не удаляется. Строки с ним прежний код
    # прочитать не сможет — перед откатом файлы КТ убирают вручную.
