"""Сборка роутов AI-ассистента: глоссарий, режим, симулятор."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.ai.presentation.routes import (
    drafts,
    glossary,
    kb,
    knowledge_gaps,
    metrics,
    regression,
    set_ai_mode,
    simulate,
    trust,
)

router = APIRouter(tags=["ai"])
router.include_router(drafts.router)
router.include_router(glossary.router)
router.include_router(knowledge_gaps.router)
router.include_router(kb.router)
router.include_router(regression.router)
router.include_router(metrics.router)
router.include_router(trust.router)
router.include_router(set_ai_mode.router)
router.include_router(simulate.router)
