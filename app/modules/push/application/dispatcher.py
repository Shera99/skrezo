"""Диспетчер пушей: событие → устройства получателей → доставка.

Работает в воркере (задача ``dispatch_push``): у него своя сессия и транспорт.
Из обработчика запроса его не зовут — туда ставится только задача (см.
:class:`app.modules.push.application.emitter.PushEmitter`), чтобы ответ не ждал
резолва получателей и доставки.

P0-b, инкремент 1 — обслуживает **sync.day**: тихий (data-only) фан-аут на ВСЕ
устройства клиники. Префы, тихие часы и маршрутизация по ролям (владелец,
менеджер, врач приёма) добавятся вместе с видимыми событиями (arrival, сводки,
деньги) — см. ``docs/PUSH_NOTIFICATIONS.md`` §3.5–3.6.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.push.domain import catalog
from app.modules.push.infrastructure.repositories.device_repository import DeviceRepository
from app.shared.push.channels import DeadTokenError, PushDeliveryError
from app.shared.push.message import PushMessage
from app.shared.push.transport import PushTransport

logger = structlog.get_logger(__name__)

#: События с правом шуметь: high-priority (FCM) / time-sensitive (APNs).
_URGENT_TYPES: frozenset[str] = frozenset(
    {
        "appointment.soon",
        "appointment.arrival",
        "appointment.moved",
        "appointment.canceled",
        "subscription.expired",
    }
)


class PushDispatcher:
    """Разослать одно событие адресатам в клинике и убрать мёртвые токены."""

    def __init__(
        self,
        session: AsyncSession,
        transport: PushTransport,
        devices: DeviceRepository,
    ) -> None:
        self._session = session
        self._transport = transport
        self._devices = devices

    async def dispatch(
        self,
        event_type: str,
        clinic_id: uuid.UUID,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> int:
        """Доставить ``event_type`` получателям клиники; вернуть число отправок.

        :param params: значения плейсхолдеров текста (``patientName``, ``time``…).
        :param data: дополнительные поля ``data`` пуша (``appointmentId``…).
        """
        recipients = await self._devices.list_by_clinic(clinic_id)
        if not recipients:
            return 0

        # Один id на всю рассылку события: два устройства одного человека
        # схлопнут дубль по ``data.id`` на клиенте.
        event_id = f"evt_{uuid.uuid4().hex}"
        collapse_key = f"{clinic_id}:{event_type}"
        urgent = event_type in _URGENT_TYPES

        sent = 0
        dead: list[str] = []
        for device in recipients:
            texts = catalog.render(event_type, device.locale, **(params or {}))
            payload: dict[str, str] = {
                "type": event_type,
                "id": event_id,
                "clinicId": str(clinic_id),
            }
            if data:
                payload.update(data)
            if texts and texts.get("speak"):
                payload["speak"] = texts["speak"]

            message = PushMessage(
                data=payload,
                title=(texts or {}).get("title") or None,
                body=(texts or {}).get("body") or None,
                collapse_key=collapse_key,
                urgent=urgent,
                android_channel="appointments-urgent" if urgent else "appointments-digest",
            )
            try:
                await self._transport.send(device.platform, device.token, message)
                sent += 1
            except DeadTokenError:
                dead.append(device.token)
            except PushDeliveryError as exc:
                # Временная ошибка одного устройства не срывает рассылку остальным.
                logger.warning("push_delivery_failed", type=event_type, error=str(exc))

        for token in dead:
            await self._devices.delete_dead(token)
        if dead:
            await self._session.commit()
        return sent
