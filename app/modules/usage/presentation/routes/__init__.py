"""Сборка роутов учёта использования (админка Skrezo)."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.usage.presentation.routes import usage

router = APIRouter(tags=["usage"])
router.include_router(usage.router)
