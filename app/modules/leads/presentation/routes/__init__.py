"""Сборка роутов модуля лидов."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.leads.presentation.routes import create_lead

router = APIRouter(tags=["leads"])
router.include_router(create_lead.router)
