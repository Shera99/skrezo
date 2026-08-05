"""Сборка роутов подписки под /clinic/subscription."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.subscriptions.presentation.routes import get_subscription

router = APIRouter(tags=["subscription"])
router.include_router(get_subscription.router)
