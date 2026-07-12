"""subscription.card_attach_deadline → nullable (гейт карты отключён).

Пока: 1 месяц бесплатно всем, без карты. Поле остаётся для будущего биллинга.

Revision ID: 0011_card_deadline
Revises: 0010_tariff
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_card_deadline"
down_revision: str | None = "0010_tariff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "subscription",
        "card_attach_deadline",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "subscription",
        "card_attach_deadline",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
