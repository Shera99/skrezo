"""Роут: GET /admin/notifications/telemetry — доставляемость пушей (Skrezo).

Ответ на вопрос ТЗ-СУ §2.5: какова РЕАЛЬНАЯ доставляемость по платформам —
вместо догадок про энергосберегайки. Алерт-порогов здесь нет: это витрина для
команды, решения (например, перевод типа с data на notification) принимает
человек по цифрам.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.push.infrastructure.repositories.delivery_repository import (
    DeliveryRepository,
)
from app.modules.push.presentation.schemas import (
    NotificationTelemetryResponse,
    TelemetryRowOut,
)

router = APIRouter()


@router.get(
    "/admin/notifications/telemetry",
    response_model=NotificationTelemetryResponse,
    summary="Доставляемость пушей по платформам",
    response_description="Сводка отправок, ошибок и подтверждённых показов",
)
async def notification_telemetry(
    days: int = Query(default=7, ge=1, le=90, description="Глубина периода, дней"),
    _admin: object = Depends(get_authenticated_user_id),
    session: AsyncSession = Depends(get_session),
) -> NotificationTelemetryResponse:
    """Сводка телеметрии: отправлено / ошибки / показано, по платформам и
    провайдерам. `displayRate` ниже ~0.5 на платформе — повод разбираться
    (агрессивная прошивка, протухшие токены, канал в лог-фолбэке)."""
    rows = await DeliveryRepository(session).summary(days)
    out = [
        TelemetryRowOut(
            platform=r.platform,
            provider=r.provider,
            sent=r.sent,
            failed=r.failed,
            displayed=r.displayed,
            display_rate=round(r.displayed / r.sent, 2) if r.sent else 0.0,
        )
        for r in rows
    ]
    return NotificationTelemetryResponse(
        days=days,
        rows=out,
        total_sent=sum(r.sent for r in rows),
        total_failed=sum(r.failed for r in rows),
        total_displayed=sum(r.displayed for r in rows),
    )
