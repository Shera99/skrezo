"""Лог-фолбэк клиента Cloud API: без кредов система работает, но не шлёт.

Тот же приём, что у OTP (``logging_channel``) и пушей: пустой токен в конфиге
не должен ронять функциональность. Входящие сохраняются как обычно (их шлёт
Meta, а не мы), «отправленное» пишется в лог, медиа не скачивается.
"""

from __future__ import annotations

import uuid

import structlog

from app.shared.whatsapp_cloud.client import DownloadedMedia, SentMessage, TemplateInfo

logger = structlog.get_logger(__name__)


class LoggingWhatsAppClient:
    """Пишет исходящие в лог вместо Graph API."""

    async def send_text(self, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        logger.info("wa_cloud_logged", to=to_phone, phone_number_id=phone_number_id, text=body)
        return SentMessage(wa_message_id=f"log-{uuid.uuid4()}")

    async def send_template(
        self,
        phone_number_id: str,
        to_phone: str,
        template_name: str,
        lang_code: str,
        variables: list[str],
    ) -> SentMessage:
        logger.info(
            "wa_cloud_template_logged",
            to=to_phone,
            template=template_name,
            lang=lang_code,
            variables=variables,
        )
        return SentMessage(wa_message_id=f"log-{uuid.uuid4()}")

    async def send_confirm_button(
        self, phone_number_id: str, to_phone: str, body: str, button_id: str, title: str
    ) -> SentMessage:
        logger.info(
            "wa_cloud_button_logged", to=to_phone, button_id=button_id, text=body
        )
        return SentMessage(wa_message_id=f"log-{uuid.uuid4()}")

    async def fetch_media(self, media_id: str) -> DownloadedMedia | None:
        logger.info("wa_cloud_media_skipped", media_id=media_id)
        return None

    async def fetch_templates(self, waba_id: str) -> list[TemplateInfo] | None:
        # None, а не []: пустой список стёр бы локальный каталог шаблонов.
        logger.info("wa_cloud_templates_skipped", waba_id=waba_id)
        return None
