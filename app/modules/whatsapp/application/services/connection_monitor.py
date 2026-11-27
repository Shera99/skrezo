"""Ежечасный присмотр за номерами, которые клиники подключили сами.

Проверка токенов (``AiHealthService.check_tokens``) говорит, какие номера Graph
API отверг. Здесь из этого делаются выводы:

* номер клиники с собственным токеном помечается «нужно переподключить», только
  если Meta явно отказала в доступе (401/403, код 190), — сетевой сбой номер не
  ломает и пометки не ставит; прошедший сбой токена пометку снимает сам;
* команда Skrezo получает в чат одно сообщение на каждый новый случай — и для
  отметок отсюда, и для отметок вебхука ``account_update``;
* недозапрошенные контакты и история приложения запрашиваются повторно, пока не
  истекли 24 часа после подключения;
* в WABA клиники досоздаются копии шаблонов напоминаний Skrezo, а каталог
  сверяется, пока Meta рассматривает шаблоны клиники, — одобрение появляется
  в кабинете само, без кнопки «Синхронизировать».
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.whatsapp.application.services.app_data_sync import request_app_data
from app.modules.whatsapp.application.services.credentials import (
    SELF_SERVICE,
    binding_token,
    clear_reconnect,
    flag_reconnect,
)
from app.modules.whatsapp.application.services.template_copies import PlatformTemplateCopier
from app.modules.whatsapp.application.use_cases.sync_templates import refresh_catalog
from app.modules.whatsapp.domain.connection import reconnect_message
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.notifications.notifier import Notifier
from app.shared.whatsapp_cloud import (
    MetaOnboardingClient,
    MetaOnboardingError,
    WhatsAppCloudClient,
)

logger = structlog.get_logger(__name__)

#: Код ошибки Meta «токен недействителен» (OAuthException).
_META_INVALID_TOKEN = 190


def auth_rejected(exc: MetaOnboardingError) -> bool:
    """Meta именно отказала в доступе, а не «не ответила»."""
    return exc.status in (401, 403) or exc.meta_code == _META_INVALID_TOKEN


@dataclass(slots=True)
class MonitorReport:
    flagged: int = 0
    cleared: int = 0
    notified: int = 0
    syncs: int = 0
    templates_copied: int = 0  # копий шаблонов Skrezo создано в WABA клиник
    catalogs: int = 0  # каталогов шаблонов сверено с Meta


class WhatsAppConnectionMonitor:
    def __init__(
        self,
        session: AsyncSession,
        onboarding: MetaOnboardingClient | None,
        notifier: Notifier | None,
        wa_client: WhatsAppCloudClient | None = None,
    ) -> None:
        self._session = session
        self._repository = WhatsAppRepository(session)
        self._onboarding = onboarding
        self._notifier = notifier
        self._wa = wa_client

    async def run(self, failed_phone_ids: list[str], now: datetime | None = None) -> MonitorReport:
        now = now or datetime.now(UTC)
        failed = set(failed_phone_ids)
        report = MonitorReport()
        bindings = await self._repository.active_bindings()
        copier = (
            PlatformTemplateCopier(self._repository, self._onboarding)
            if self._onboarding is not None
            else None
        )
        for binding in bindings:
            own_token = binding.connected_via == SELF_SERVICE and binding.access_token_enc
            if own_token and binding.phone_number_id in failed:
                if await self._access_denied(binding) and flag_reconnect(
                    binding, "token_invalid", now
                ):
                    report.flagged += 1
            elif (
                own_token
                and binding.needs_reconnect_reason == "token_invalid"
                and binding.phone_number_id not in failed
            ):
                clear_reconnect(binding)
                report.cleared += 1
            if self._onboarding is not None:
                report.syncs += await request_app_data(binding, self._onboarding, now)
            # Шаблоны — у работающего номера: своим токеном клиники или, у
            # подключённого вручную, общим токеном Skrezo (копии нужны и ему,
            # если номер живёт в WABA клиники, а не в WABA Skrezo).
            if binding.phone_number_id not in failed and (
                own_token or binding.connected_via != SELF_SERVICE
            ):
                await self._templates(binding, copier, report)
        report.notified = await self._notify_team(bindings, now)
        await self._session.commit()
        return report

    async def _templates(
        self,
        binding: ClinicWhatsApp,
        copier: PlatformTemplateCopier | None,
        report: MonitorReport,
    ) -> None:
        """Шаблоны номера клиники: недостающие копии Skrezo и решение Meta по ним.

        Каталог сверяется, только когда есть что узнать: появились копии или
        Meta ещё рассматривает какой-то шаблон, — остальные номера Graph не дёргают.
        """
        copied = await copier.copy_missing(binding) if copier is not None else None
        report.templates_copied += copied or 0
        if self._wa is None:
            return
        if copied is None and not await self._repository.has_pending_templates(binding.clinic_id):
            return
        if await refresh_catalog(self._repository, self._wa, binding):
            report.catalogs += 1

    async def _access_denied(self, binding: ClinicWhatsApp) -> bool:
        """Переспросить Meta о номере: отказ в доступе отличить от сетевого сбоя."""
        token = binding_token(binding)
        if self._onboarding is None or token is None:
            return False
        try:
            await self._onboarding.phone_number(binding.phone_number_id, token)
        except MetaOnboardingError as exc:
            return auth_rejected(exc)
        return False  # номер ответил — сбой был временным

    async def _notify_team(self, bindings: list[ClinicWhatsApp], now: datetime) -> int:
        pending = [
            b
            for b in bindings
            if b.needs_reconnect_at is not None and b.reconnect_notified_at is None
        ]
        if not pending:
            return 0
        names = await self._repository.clinic_names([b.clinic_id for b in pending])
        lines = [
            f"• {names.get(b.clinic_id, b.clinic_id)} ({b.display_phone}) — "
            f"{reconnect_message(b.needs_reconnect_reason)}"
            for b in pending
        ]
        text = "WhatsApp клиник нужно переподключить:\n" + "\n".join(lines)
        if self._notifier is not None:
            await self._notifier.notify_team(text, event="whatsapp_reconnect")
        logger.warning("wa_reconnect_needed", clinics=[str(b.clinic_id) for b in pending])
        for binding in pending:
            binding.reconnect_notified_at = now
        return len(pending)
