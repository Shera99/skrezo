"""Перемоделирование тарифа клиники: пакеты + over_price + целые версии.

Заменяет модель ступеней (`tariff_tier`, цена за место) на модель пакетов
(`tariff_package`, фиксированная цена за клинику до `up_to` мест) + доплату за
место сверх последнего пакета (`tariff_version.over_price`, «Сеть»). Версии
получают целочисленный номер `version` (растёт по типу от 1).

Revision ID: 0016_tariff_packages
Revises: 0015_account_password
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_tariff_packages"
down_revision: str | None = "0015_account_password"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Номер версии + доплата за место сверх последнего пакета.
    op.add_column("tariff_version", sa.Column("version", sa.Integer(), nullable=True))
    op.add_column("tariff_version", sa.Column("over_price", sa.Integer(), nullable=True))
    # Бэкфилл номеров версий: по типу, в порядке created_at.
    op.execute(
        """
        UPDATE tariff_version tv SET version = seq.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY type ORDER BY created_at) AS rn
            FROM tariff_version
        ) seq
        WHERE tv.id = seq.id
        """
    )
    op.alter_column("tariff_version", "version", nullable=False)

    # 2) Ступени → пакеты. Старые ступени (цена за место) несовместимы с новой
    #    моделью — таблица пересоздаётся; клинические версии заведут заново.
    op.drop_table("tariff_tier")
    op.create_table(
        "tariff_package",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tariff_version_id",
            sa.Uuid(),
            sa.ForeignKey("tariff_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("up_to", sa.Integer(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tariff_package")
    op.create_table(
        "tariff_tier",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tariff_version_id",
            sa.Uuid(),
            sa.ForeignKey("tariff_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seats_from", sa.Integer(), nullable=False),
        sa.Column("seats_to", sa.Integer(), nullable=True),
        sa.Column("price_per_seat", sa.Integer(), nullable=False),
    )
    op.drop_column("tariff_version", "over_price")
    op.drop_column("tariff_version", "version")
