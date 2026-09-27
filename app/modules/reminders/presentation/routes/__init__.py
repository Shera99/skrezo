"""Сборка роутов напоминаний под /clinic/reminders."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.reminders.presentation.routes import create_mark, delete_mark, list_marks

router = APIRouter(tags=["reminders"])
router.include_router(create_mark.router)
router.include_router(list_marks.router)
router.include_router(delete_mark.router)
