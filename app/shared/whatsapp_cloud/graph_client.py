"""Боевой клиент Graph API: отправка сообщений и скачивание медиа.

Скачивание медиа — два запроса по правилам Meta: сначала метаданные по
``media_id`` (там одноразовый URL и MIME), затем сам файл по этому URL с тем же
токеном. URL живёт минуты, поэтому скачивание происходит сразу при обработке
вебхука, а не «когда-нибудь по запросу».
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.shared.whatsapp_cloud.client import DownloadedMedia, SentMessage, WhatsAppSendError

logger = structlog.get_logger(__name__)

_TIMEOUT = 15.0


class GraphWhatsAppClient:
    """Клиент Cloud API поверх httpx (async)."""

    def __init__(self, api_url: str, access_token: str) -> None:
        self._api_url = api_url.rstrip("/")
        self._token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _post_message(self, phone_number_id: str, payload: dict[str, Any]) -> SentMessage:
        url = f"{self._api_url}/{phone_number_id}/messages"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:  # сеть/таймаут — тоже «не отправлено»
            raise WhatsAppSendError(str(exc)) from exc
        if resp.status_code >= 400:
            # Тело ошибки Meta — в лог целиком: код у них внутри JSON, и без
            # него «400» не говорит ничего (окно закрыто? номер не в WA?).
            logger.error("wa_cloud_send_failed", status=resp.status_code, body=resp.text[:500])
            raise WhatsAppSendError(f"Graph API {resp.status_code}")
        data = resp.json()
        try:
            return SentMessage(wa_message_id=data["messages"][0]["id"])
        except (KeyError, IndexError) as exc:
            raise WhatsAppSendError("Graph API: нет id сообщения в ответе") from exc

    async def send_text(self, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        return await self._post_message(
            phone_number_id,
            {
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "text",
                "text": {"body": body},
            },
        )

    async def send_template(
        self,
        phone_number_id: str,
        to_phone: str,
        template_name: str,
        lang_code: str,
        variables: list[str],
    ) -> SentMessage:
        components: list[dict[str, Any]] = []
        if variables:
            components.append(
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in variables],
                }
            )
        return await self._post_message(
            phone_number_id,
            {
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": lang_code},
                    "components": components,
                },
            },
        )

    async def fetch_media(self, media_id: str) -> DownloadedMedia | None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                meta = await client.get(f"{self._api_url}/{media_id}", headers=self._headers())
                if meta.status_code >= 400:
                    logger.error(
                        "wa_cloud_media_meta_failed",
                        status=meta.status_code,
                        media_id=media_id,
                    )
                    return None
                info = meta.json()
                blob = await client.get(info["url"], headers=self._headers())
                if blob.status_code >= 400:
                    logger.error(
                        "wa_cloud_media_download_failed",
                        status=blob.status_code,
                        media_id=media_id,
                    )
                    return None
                return DownloadedMedia(
                    content=blob.content,
                    mime=info.get("mime_type", "application/octet-stream"),
                )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # Медиа — не повод ронять обработку сообщения: текст и факт
            # голосового сохранятся, файл просто не скачается.
            logger.error("wa_cloud_media_error", media_id=media_id, error=str(exc))
            return None
