"""clinic.city_id → nullable (для соло город заполняется в онбординге).

Revision ID: 0007_clinic_city
Revises: 0006_i18n
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_clinic_city"
down_revision: str | None = "0006_i18n"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("clinic", "city_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.alter_column("clinic", "city_id", existing_type=sa.Uuid(), nullable=False)
