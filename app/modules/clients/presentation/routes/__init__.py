"""Сборка роутов раздела «Клиенты» под /admin/clients."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.clients.presentation.routes import (
    add_doctor,
    archive_client,
    change_admin_phone,
    create_account,
    get_client,
    list_clients,
    phone_check,
    remove_doctor,
    reset_admin_password,
    status_counts,
    update_client,
    update_doctor,
)

router = APIRouter(tags=["clients"])
router.include_router(list_clients.router)
router.include_router(create_account.router)
# Статичные пути — до параметрического /{clinic_id}, иначе он их перехватит.
router.include_router(phone_check.router)
router.include_router(status_counts.router)
router.include_router(get_client.router)
router.include_router(update_client.router)
router.include_router(change_admin_phone.router)
router.include_router(reset_admin_password.router)
router.include_router(archive_client.router)
# Врачи со стороны админа.
router.include_router(add_doctor.router)
router.include_router(update_doctor.router)
router.include_router(remove_doctor.router)
