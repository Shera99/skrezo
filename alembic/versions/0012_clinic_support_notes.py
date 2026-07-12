"""clinic.support_notes — заметки саппорта (платформенная зона).

Revision ID: 0012_support_notes
Revises: 0011_card_deadline
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_support_notes"
down_revision: str | None = "0011_card_deadline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("clinic", sa.Column("support_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("clinic", "support_notes")
