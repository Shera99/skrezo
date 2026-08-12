"""Сборка роутов раздела «Аккаунты» под /admin/accounts."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.accounts.presentation.routes import block_account, list_accounts

router = APIRouter(tags=["accounts"])
router.include_router(list_accounts.router)
router.include_router(block_account.router)
