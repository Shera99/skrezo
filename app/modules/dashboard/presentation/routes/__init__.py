"""Сборка роутов дашборда под /admin/dashboard."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.dashboard.presentation.routes import get_summary, get_trends

router = APIRouter(tags=["dashboard"])
router.include_router(get_summary.router)
router.include_router(get_trends.router)
