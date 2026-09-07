"""Ступени доверия ассистента (Ф7, инкремент 1).

Режим ``clinic.ai_mode`` остаётся источником истины, но у смены появляется
память: когда и почему (владелец / гейт / мониторинг / команда Skrezo) и
какой была ступень до выключения — чтобы «включить обратно» возвращало
заработанную ступень, а не сбрасывало в «Наблюдаю».

Право ``assistant.manage`` — настройки ассистента: ступени и выключатель.
Владелец имеет его всегда (is_owner), остальным выдаётся через роли.

Revision ID: 0056_trust_ladder
Revises: 0055_ai_metrics_index
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056_trust_ladder"
down_revision = "0055_ai_metrics_index"
branch_labels = None
depends_on = None

_PERMISSION = "assistant.manage"
_PERMISSION_DESCRIPTION = "Настройки ассистента: ступени доверия и выключатель"


def upgrade() -> None:
    op.add_column(
        "clinic", sa.Column("ai_mode_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("clinic", sa.Column("ai_mode_reason", sa.Text(), nullable=True))
    op.add_column("clinic", sa.Column("ai_mode_before_off", sa.Text(), nullable=True))
    # На чистой БД право засеет каталог (0004), на живой — вставляем идемпотентно.
    op.execute(
        f"""
        INSERT INTO permission (id, key, group_key, description, sort_order, in_trial)
        SELECT gen_random_uuid(), '{_PERMISSION}', 'settings',
               '{_PERMISSION_DESCRIPTION}',
               (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM permission), true
        WHERE NOT EXISTS (SELECT 1 FROM permission WHERE key = '{_PERMISSION}')
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM role_permission rp
        USING permission p
        WHERE rp.permission_id = p.id AND p.key = '{_PERMISSION}'
        """
    )
    op.execute(f"DELETE FROM permission WHERE key = '{_PERMISSION}'")
    op.drop_column("clinic", "ai_mode_before_off")
    op.drop_column("clinic", "ai_mode_reason")
    op.drop_column("clinic", "ai_mode_changed_at")
