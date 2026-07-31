"""Сборка роутов доступа: роли клиники (/clinic) и триал-функции (/admin).

Контуры разные: роли настраивает сама клиника по праву ``roles.manage``,
триал-функции — только команда Skrezo.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.access.presentation.routes import (
    create_role,
    delete_role,
    get_trial_features,
    list_permissions,
    list_roles,
    update_role,
    update_trial_features,
)

router = APIRouter(tags=["roles"])
router.include_router(list_roles.router)
router.include_router(list_permissions.router)
router.include_router(create_role.router)
router.include_router(update_role.router)
router.include_router(delete_role.router)

admin_router = APIRouter(tags=["trial-features"])
admin_router.include_router(get_trial_features.router)
admin_router.include_router(update_trial_features.router)
