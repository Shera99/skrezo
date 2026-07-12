"""Уведомление добавленного врача (WhatsApp «войдите по телефону и паролю»).

v1 — заглушка (провайдер не выбран): логируем факт. Временный пароль работнику
передаёт добавивший его владелец/менеджер (возвращается в ответе API).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class StaffNotificationService:
    async def notify_staff_added(self, *, phone: str, clinic_name: str) -> None:
        # TODO(phase позже): WhatsApp «Вас добавили в {clinic_name}, войдите по паролю».
        logger.info("staff_added", phone=phone, clinic=clinic_name)
