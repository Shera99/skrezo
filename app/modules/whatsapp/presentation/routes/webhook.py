"""Роуты вебхука Meta: GET (верификация подписки) и POST (события).

Единственные публичные ручки модуля — их зовёт Meta, а не наш клиент.
Требование Meta: 200 быстрее 5 секунд, иначе ретраи и молчаливое отключение
подписки. Поэтому POST только проверяет подпись и ставит обработку в очередь;
если очередь недоступна (Redis лёг) — обрабатывает на месте: сообщение
пациента терять нельзя, а Meta повторной доставкой дубля не создаст
(``wa_message_id`` уникален).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import ForbiddenError
from app.core.jobs import PROCESS_WA_INBOUND, JobQueue, get_job_queue
from app.modules.whatsapp.application.services.inbound import InboundProcessor
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.modules.whatsapp.presentation.deps import get_whatsapp_client
from app.shared.whatsapp_cloud import WhatsAppCloudClient

logger = structlog.get_logger(__name__)

router = APIRouter()

_FORBIDDEN: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Подпись не сошлась либо проверочный токен неверен",
        "content": {
            "application/json": {
                "example": {"error": "invalid_signature", "message": "Подпись не сошлась"}
            }
        },
    },
}


@router.get(
    "/whatsapp/webhook",
    response_class=PlainTextResponse,
    summary="Верификация вебхука (вызывает Meta)",
    response_description="Эхо `hub.challenge` — подписка подтверждена",
    responses=_FORBIDDEN,
)
async def verify_webhook(
    mode: str = Query(alias="hub.mode", description="Всегда `subscribe`"),
    verify_token: str = Query(
        alias="hub.verify_token", description="Токен, заданный при настройке подписки"
    ),
    challenge: str = Query(alias="hub.challenge", description="Строка, которую надо вернуть"),
) -> str:
    """Подтвердить подписку вебхука в кабинете Meta.

    Токен сверяется с ``WA_CLOUD_VERIFY_TOKEN``; пустая настройка означает
    «подписку не подтверждаем вовсе» — случайно открытой верификации нет.
    """
    if (
        mode != "subscribe"
        or not settings.wa_cloud_verify_token
        or not hmac.compare_digest(verify_token, settings.wa_cloud_verify_token)
    ):
        raise ForbiddenError("Проверочный токен неверен", code="invalid_verify_token")
    return challenge


def _signature_valid(secret: str, raw: bytes, header: str | None) -> bool:
    """Проверить ``X-Hub-Signature-256`` (HMAC-SHA256 от тела запроса)."""
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header.removeprefix("sha256="))


@router.post(
    "/whatsapp/webhook",
    summary="События WhatsApp (вызывает Meta)",
    response_description="Подтверждение приёма (всегда сразу)",
    responses=_FORBIDDEN,
)
async def receive_webhook(
    request: Request,
    queue: JobQueue = Depends(get_job_queue),
    session: AsyncSession = Depends(get_session),
    client: WhatsAppCloudClient = Depends(get_whatsapp_client),
) -> dict[str, bool]:
    """Принять пачку событий: входящие сообщения, эхо Coexistence, статусы.

    Подпись проверяется по сырому телу до разбора JSON. Пустой
    ``WA_CLOUD_APP_SECRET`` отключает проверку — только для локальной
    разработки, в бою секрет обязателен.
    """
    raw = await request.body()
    if settings.wa_cloud_app_secret:
        if not _signature_valid(
            settings.wa_cloud_app_secret, raw, request.headers.get("X-Hub-Signature-256")
        ):
            raise ForbiddenError("Подпись не сошлась", code="invalid_signature")
    else:
        logger.warning("wa_webhook_signature_skipped")

    try:
        payload = json.loads(raw)
    except ValueError:
        # Мусор с валидной подписью: 200, чтобы Meta не копила ретраи, след — в логе.
        logger.error("wa_webhook_bad_json", size=len(raw))
        return {"received": True}

    if not await queue.enqueue(PROCESS_WA_INBOUND, payload):
        # Очереди нет — обрабатываем на месте: вебхук уже за пределами 5-секундного
        # правила Meta не выйдет (разбор и вставка — миллисекунды), а сообщение
        # пациента дороже симметрии с фоновым путём.
        processor = InboundProcessor(session, WhatsAppRepository(session), client)
        await processor.process(payload)
    return {"received": True}
