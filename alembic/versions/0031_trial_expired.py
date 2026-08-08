"""Статус подписки ``trial_expired``: триал кончился, оплаты не было.

Раньше статус после конца триала не менялся никто и никогда: клиника вечно
числилась в ``trial``, а `GET /clinic/subscription` считал истечение на лету.
Из-за этого админка, счётчики статусов и дашборд показывали клиента активным
триальщиком спустя месяцы.

Отдельное значение, а не переиспользование ``past_due``/``awaiting_card``:
у тех своя причина («списание не прошло», «карта не привязана»), а здесь
оплаты не было вовсе — биллинга ещё нет. Врать статусом ради экономии одного
значения enum нельзя: по нему потом считают воронку.

⚠️ Доступ клиники закрывает НЕ этот статус, а политика подписки по
``trial_ends_at`` — иначе упавший воркер раздавал бы бесплатный доступ.

Revision ID: 0031_trial_expired
Revises: 0030_discount
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031_trial_expired"
down_revision: str | None = "0030_discount"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE нельзя использовать в транзакции, где значение
    # создано, а alembic гонит весь upgrade одной транзакцией (см. 0024, 0025).
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE subscription_status "
            "ADD VALUE IF NOT EXISTS 'trial_expired' AFTER 'trial'"
        )


def downgrade() -> None:
    # Значение enum в Postgres не удаляется (нет ALTER TYPE ... DROP VALUE),
    # поэтому возвращаем данные в валидное для прежнего набора состояние:
    # истёкший триал — это всё ещё триал, просто закончившийся.
    op.execute(
        "UPDATE subscription SET status = 'trial'::subscription_status "
        "WHERE status = 'trial_expired'::subscription_status"
    )
    op.execute(
        "UPDATE subscription_event SET to_status = 'trial'::subscription_status "
        "WHERE to_status = 'trial_expired'::subscription_status"
    )
    op.execute(
        "UPDATE subscription_event SET from_status = 'trial'::subscription_status "
        "WHERE from_status = 'trial_expired'::subscription_status"
    )
