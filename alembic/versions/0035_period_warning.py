"""Отметка предупреждения о конце оплаченного периода.

Зеркало ``trial_warning_sent_at``, но поле отдельное: триал бывает один раз, а
оплаченный период повторяется, и отметку нужно сбрасывать при каждом продлении.
Общее поле на оба случая означало бы, что после первой же оплаты клиника больше
никогда не получит предупреждения — ни о периоде, ни о чём-либо ещё.

Revision ID: 0035_period_warning
Revises: 0034_job_run_and_trial_warning
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_period_warning"
down_revision: str | None = "0034_job_run_and_trial_warning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscription",
        sa.Column("period_warning_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription", "period_warning_sent_at")
