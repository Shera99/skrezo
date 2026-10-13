"""Сборка роутов голосового помощника приложения."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.llm.presentation.routes import generate

router = APIRouter(tags=["llm"])
router.include_router(generate.router)
