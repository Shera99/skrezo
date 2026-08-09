"""Журнал запусков фоновых задач и отметка предупреждения о конце триала.

``job_run`` отвечает на вопрос «работает ли воркер»: по логам это видно, только
если знаешь, что искать, а по таблице видно и обратное — джоба не запускалась
вовсе. Строка пишется дважды (``running`` в начале, исход в конце), иначе
зависшая или убитая на полпути задача не оставляет следа.

``subscription.trial_warning_sent_at`` — отметка «владельцу сказали, что триал
кончается». Джоба ходит ежедневно, предупреждение уместно один раз; в
``subscription_event`` такую отметку класть нельзя — там лог переходов статуса,
а предупреждение статус не меняет.

Revision ID: 0034_job_run_and_trial_warning
Revises: 0033_appointment_item_position
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_job_run_and_trial_warning"
down_revision: str | None = "0033_appointment_item_position"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_name", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("running", "ok", "failed", name="job_run_status"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Экран состояния спрашивает «последние запуски», обычно по одной джобе.
    op.create_index(
        "ix_job_run_name_started",
        "job_run",
        ["job_name", sa.literal_column("started_at DESC")],
    )
    op.add_column(
        "subscription",
        sa.Column("trial_warning_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription", "trial_warning_sent_at")
    op.drop_index("ix_job_run_name_started", table_name="job_run")
    op.drop_table("job_run")
    # Тип enum создаётся вместе с таблицей, но не удаляется вместе с ней.
    sa.Enum(name="job_run_status").drop(op.get_bind(), checkfirst=True)
