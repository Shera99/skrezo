"""Сборка роутов WhatsApp: вебхук Meta, диалоги клиники, привязка номера."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.whatsapp.presentation.routes import (
    connect_whatsapp,
    get_media,
    get_messages,
    list_conversations,
    send_message,
    templates,
    webhook,
)

router = APIRouter(tags=["whatsapp"])
router.include_router(webhook.router)
router.include_router(list_conversations.router)
router.include_router(get_messages.router)
router.include_router(send_message.router)
router.include_router(templates.router)
router.include_router(get_media.router)
router.include_router(connect_whatsapp.router)
