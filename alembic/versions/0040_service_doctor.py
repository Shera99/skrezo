"""Какие врачи выполняют услугу.

До этого прайс ничего не знал о врачах: на «Имплантацию» записывали к терапевту,
а «ближайшее свободное на эту услугу» фронт собирал, опрашивая слоты каждого
врача по очереди.

Данные не мигрируем намеренно: отсутствие строк означает «делают все», поэтому
существующие услуги продолжают работать ровно как раньше, а соло-кабинет не
трогается вовсе.

Revision ID: 0040_service_doctor
Revises: 0039_account_blocking
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_service_doctor"
down_revision: str | None = "0039_account_blocking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_doctor",
        sa.Column(
            "service_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("service.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "membership_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("membership.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Обратный поиск «что умеет этот врач»; прямой закрыт первичным ключом.
    op.create_index("ix_service_doctor_membership", "service_doctor", ["membership_id"])


def downgrade() -> None:
    op.drop_index("ix_service_doctor_membership", table_name="service_doctor")
    op.drop_table("service_doctor")
