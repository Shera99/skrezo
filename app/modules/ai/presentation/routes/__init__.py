"""Сборка роутов AI-ассистента: глоссарий, режим, симулятор."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.ai.presentation.routes import (
    drafts,
    glossary,
    kb,
    knowledge_gaps,
    regression,
    set_ai_mode,
    simulate,
)

router = APIRouter(tags=["ai"])
router.include_router(drafts.router)
router.include_router(glossary.router)
router.include_router(knowledge_gaps.router)
router.include_router(kb.router)
router.include_router(regression.router)
router.include_router(set_ai_mode.router)
router.include_router(simulate.router)
