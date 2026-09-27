"""Клиент Cloud API для пробного прогона: наружу не уходит ничего.

Симулятор ассистента (``POST /admin/ai/simulate-inbound`` с ``dryRun``) гоняет
боевой конвейер, но ответ бота, кнопки и шаблоны пациенту уходить не должны.
Подменить клиента проще и надёжнее, чем ветвить каждое место отправки:
конвейер ведёт себя как в режиме ``auto``, а «отправка» здесь только пишется
в лог. Идентификаторы сообщений получают префикс :data:`DRY_RUN_PREFIX` — по
нему симулятор после прогона снимает с них признак «отправлено».

Чтение (медиа, токен, шаблоны) делегируется настоящему клиенту: оно ничего не
меняет и нужно конвейеру таким же, как в бою.
"""

from __future__ import annotations

import uuid

import structlog

from app.shared.whatsapp_cloud.client import (
    DownloadedMedia,
    SentMessage,
    TemplateInfo,
    WhatsAppCloudClient,
)

logger = structlog.get_logger(__name__)

#: Префикс ``wa_message_id`` у сообщений, «отправленных» в пробном прогоне.
DRY_RUN_PREFIX = "dry-"


class DryRunWhatsAppClient:
    """Отправку пишет в лог, чтение делегирует обёрнутому клиенту."""

    def __init__(self, inner: WhatsAppCloudClient) -> None:
        self._inner = inner

    @staticmethod
    def _sent(kind: str, **fields: object) -> SentMessage:
        logger.info("wa_cloud_dry_run", kind=kind, **fields)
        return SentMessage(wa_message_id=f"{DRY_RUN_PREFIX}{uuid.uuid4()}")

    async def send_text(self, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        return self._sent("text", to=to_phone, text=body)

    async def send_template(
        self,
        phone_number_id: str,
        to_phone: str,
        template_name: str,
        lang_code: str,
        variables: list[str],
        buttons: list[str] | None = None,
    ) -> SentMessage:
        return self._sent(
            "template", to=to_phone, template=template_name, lang=lang_code, variables=variables
        )

    async def send_confirm_button(
        self, phone_number_id: str, to_phone: str, body: str, button_id: str, title: str
    ) -> SentMessage:
        return self._sent("button", to=to_phone, button_id=button_id, text=body)

    async def fetch_media(self, media_id: str) -> DownloadedMedia | None:
        return await self._inner.fetch_media(media_id)

    async def check_token(self, phone_number_id: str) -> bool:
        return await self._inner.check_token(phone_number_id)

    async def fetch_templates(self, waba_id: str) -> list[TemplateInfo] | None:
        return await self._inner.fetch_templates(waba_id)
