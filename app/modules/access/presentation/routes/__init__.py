"""Сборка админ-роутов доступа (настройка триал-функций) под /admin."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.access.presentation.routes import get_trial_features, update_trial_features

admin_router = APIRouter(tags=["trial-features"])
admin_router.include_router(get_trial_features.router)
admin_router.include_router(update_trial_features.router)
