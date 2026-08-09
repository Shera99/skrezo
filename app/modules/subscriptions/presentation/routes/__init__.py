"""Сборка роутов подписки: клиентский контур и админ-контур Skrezo."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.subscriptions.presentation.routes import (
    activate_subscription,
    block_subscription,
    get_subscription,
)

router = APIRouter(tags=["subscription"])
router.include_router(get_subscription.router)

# Управление подпиской — только команда Skrezo: пока платёжного провайдера нет,
# оплату отмечает человек. Отдельный роутер, чтобы тег в OpenAPI не смешивал
# клиентскую ручку с админскими.
admin_router = APIRouter(tags=["subscription-admin"])
admin_router.include_router(activate_subscription.router)
admin_router.include_router(block_subscription.router)
