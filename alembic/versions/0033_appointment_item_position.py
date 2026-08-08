"""Порядок позиций в чеке приёма.

Позиции сортировались по ``created_at``, а он у всех строк одного приёма
ОДИНАКОВ: ``now()`` в Postgres — время начала транзакции, а чек пишется одной.
Дальше сортировка падала на ``id`` (случайный UUID), и это давало два дефекта:

* карточка приёма показывала позиции не в том порядке, в каком их прислал
  клиент, — «кариес 36 + чистка» могли меняться местами между запросами;
* синхронизация одонтограммы не могла ответить, какая работа по зубу
  **последняя**: запломбировали и удалили в одном приёме — состояние зуба
  зависело от плана запроса.

``position`` — порядковый номер позиции в чеке (с нуля), проставляется при
записи. Существующим строкам раздаётся по прежнему порядку сортировки, чтобы
уже показанные чеки не перетасовались.

Revision ID: 0033_appointment_item_position
Revises: 0032_service_tooth_outcome
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_appointment_item_position"
down_revision: str | None = "0032_service_tooth_outcome"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "appointment_item",
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.execute(
        """
        UPDATE appointment_item i
        SET position = numbered.rn - 1
        FROM (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY appointment_id ORDER BY created_at, id
                   ) AS rn
            FROM appointment_item
        ) numbered
        WHERE i.id = numbered.id
        """
    )


def downgrade() -> None:
    op.drop_column("appointment_item", "position")
