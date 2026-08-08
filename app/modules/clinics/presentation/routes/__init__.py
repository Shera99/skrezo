"""Сборка роутов клиники под /clinic/settings.

Создание клиники живёт в разделе «Клиенты» (`POST /admin/clients`) — его ведёт
команда Skrezo. Здесь модуль публикует то, чем клиника распоряжается сама:
собственные настройки (название, контакты, город, язык, таймзона).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.clinics.presentation.routes import get_settings, update_settings

router = APIRouter(tags=["clinic-settings"])
router.include_router(get_settings.router)
router.include_router(update_settings.router)
