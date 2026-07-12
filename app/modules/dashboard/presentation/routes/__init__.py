"""Сборка роутов дашборда под /admin/dashboard."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.dashboard.presentation.routes import get_summary

router = APIRouter(tags=["dashboard"])
router.include_router(get_summary.router)
