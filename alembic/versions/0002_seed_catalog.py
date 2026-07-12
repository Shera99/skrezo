"""Сид справочников: города КГ и стоматологические специализации.

Revision ID: 0002_seed_catalog
Revises: 0001_initial
Create Date: 2026-07-05
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_seed_catalog"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Крупные города Кыргызстана (список расширяется по мере надобности).
CITIES: list[str] = [
    "Бишкек",
    "Ош",
    "Джалал-Абад",
    "Каракол",
    "Токмок",
    "Кара-Балта",
    "Узген",
    "Нарын",
    "Талас",
    "Баткен",
    "Кызыл-Кия",
    "Кант",
    "Чолпон-Ата",
]

# Стоматологические специализации.
SPECIALIZATIONS: list[str] = [
    "Стоматолог-терапевт",
    "Стоматолог-хирург",
    "Стоматолог-ортопед",
    "Ортодонт",
    "Пародонтолог",
    "Эндодонт",
    "Детский стоматолог",
    "Имплантолог",
    "Гигиенист",
]


def _table(name: str) -> sa.Table:
    return sa.table(
        name,
        sa.column("id", sa.Uuid),
        sa.column("name", sa.Text),
        sa.column("sort_order", sa.Integer),
        sa.column("active", sa.Boolean),
    )


def upgrade() -> None:
    op.bulk_insert(
        _table("city"),
        [
            {"id": uuid.uuid4(), "name": name, "sort_order": i, "active": True}
            for i, name in enumerate(CITIES)
        ],
    )
    op.bulk_insert(
        _table("specialization"),
        [
            {"id": uuid.uuid4(), "name": name, "sort_order": i, "active": True}
            for i, name in enumerate(SPECIALIZATIONS)
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM specialization")
    op.execute("DELETE FROM city")
