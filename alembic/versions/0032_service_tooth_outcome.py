"""Исход услуги для зуба: одонтограмма обновляется сама при завершении приёма.

Раньше карта зуба жила отдельно от истории: вылечили кариес на 36 — в
одонтограмме он так и оставался кариозным, пока кто-то не поправит через
``PUT /clinic/patients/{id}/teeth``. Расхождение копилось, а решает его врач,
глядя на две выдачи сразу.

``service.tooth_outcome`` — в какое состояние услуга приводит зуб («Пломба» →
``filling``, «Коронка» → ``crown``, «Удаление» → ``missing``). NULL значит
«состояние зуба не меняет» (чистка, консультация, снимок) и остаётся дефолтом:
выводить исход из НАЗВАНИЯ услуги нельзя — прайс ведёт клиника, и угадывание по
строке ошибётся на первой же «Реставрации II класса».

Колонка nullable и без бэкфилла: у существующих услуг исход неизвестен, и
проставить его может только клиника. До настройки синк просто не срабатывает —
это честнее, чем угаданный статус в медицинской карте.

Revision ID: 0032_service_tooth_outcome
Revises: 0031_trial_expired
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_service_tooth_outcome"
down_revision: str | None = "0031_trial_expired"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False: тип tooth_status уже есть в схеме (одонтограмма, 0019).
_TOOTH_STATUS = postgresql.ENUM(name="tooth_status", create_type=False)


def upgrade() -> None:
    op.add_column("service", sa.Column("tooth_outcome", _TOOTH_STATUS, nullable=True))


def downgrade() -> None:
    op.drop_column("service", "tooth_outcome")
