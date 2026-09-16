"""Служебные шаблоны Meta вычищены из каталогов клиник.

Тестовый ``hello_world`` Meta заводит в каждом WABA; синхронизация без
фильтра сохраняла его как одобренный utility-шаблон, и кабинет предлагал его
в закрытое окно 24 часов. Код больше не сохраняет служебные шаблоны и
отвергает их отправку (``WA_SERVICE_TEMPLATE_NAMES``); уже сохранённые строки
удаляются здесь, чтобы каталог не ждал следующей синхронизации. Downgrade
ничего не восстанавливает: следующая сверка с Meta вернула бы их сама.

Revision ID: 0065_drop_service_templates
Revises: 0064_one_conversation_per_phone
"""

from __future__ import annotations

from alembic import op

revision = "0065_drop_service_templates"
down_revision = "0064_one_conversation_per_phone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM whatsapp_template WHERE lower(meta_name) = 'hello_world'")


def downgrade() -> None:
    pass
