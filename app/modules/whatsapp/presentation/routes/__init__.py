"""Сборка роутов WhatsApp: вебхук Meta, диалоги клиники, привязка номера.

Отдельный ``admin_router`` — платформенный контур Skrezo: общие шаблоны и их
подписи для кабинетов клиник (только ``is_platform_admin``).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.whatsapp.presentation.routes import (
    admin_templates,
    connect_whatsapp,
    conversation_read,
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
router.include_router(conversation_read.router)
router.include_router(get_messages.router)
router.include_router(send_message.router)
router.include_router(templates.router)
router.include_router(get_media.router)
router.include_router(connect_whatsapp.router)

admin_router = APIRouter(tags=["whatsapp-admin"])
admin_router.include_router(admin_templates.router)
