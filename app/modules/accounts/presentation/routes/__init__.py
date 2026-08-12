"""Сборка роутов раздела «Аккаунты» под /admin/accounts."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.accounts.presentation.routes import list_accounts

router = APIRouter(tags=["accounts"])
router.include_router(list_accounts.router)
