"""Копии шаблонов напоминаний Skrezo в WABA клиники, подключившей номер сама.

Шаблон WhatsApp принадлежит одному WABA, и номер отправляет только шаблоны
своего WABA. Шаблоны напоминаний (визит, долг, повторный осмотр) команда
регистрирует в WABA Skrezo (``WA_TEMPLATE_SOURCE_WABA_ID``) — ими пользуются
номера, подключённые вручную. Номер, который клиника подключила через окно
Meta, живёт в её собственном WABA, поэтому туда создаются копии: те же имя,
язык, категория и компоненты, что у одобренного оригинала. Meta проверяет копию
заново; статус подтягивает сверка каталога (сразу и ежечасным монитором).

Копируется только недостающее: пара (имя, язык), которой нет ни в локальном
каталоге клиники, ни в её WABA. Поэтому повторять безопасно, а шаблон,
зарегистрированный в WABA Skrezo позже, доедет до клиник сам при следующем
проходе монитора. Ни один сбой Meta не роняет вызывающего: подключение номера
важнее шаблонов, и монитор повторит через час.
"""

from __future__ import annotations

import structlog

from app.core.config import settings
from app.modules.whatsapp.application.services.credentials import SELF_SERVICE, binding_token
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.whatsapp_cloud import MetaOnboardingClient, MetaOnboardingError, TemplateDefinition

logger = structlog.get_logger(__name__)


def copied_template_names() -> frozenset[str]:
    """Имена шаблонов, без которых не работают напоминания ассистента."""
    names = (
        settings.wa_visit_reminder_template,
        settings.wa_debt_reminder_template,
        settings.wa_recall_reminder_template,
    )
    return frozenset(name.strip() for name in names if name.strip())


class PlatformTemplateCopier:
    """Создаёт в WABA клиники недостающие копии одобренных шаблонов Skrezo.

    Один экземпляр — один проход: шаблоны источника спрашиваются у Meta один
    раз и переиспользуются для всех номеров прохода монитора.
    """

    def __init__(self, repository: WhatsAppRepository, onboarding: MetaOnboardingClient) -> None:
        self._repository = repository
        self._onboarding = onboarding
        self._source: list[TemplateDefinition] | None = None

    async def copy_missing(self, binding: ClinicWhatsApp) -> int | None:
        """Создать недостающие копии; вернуть их число.

        ``None`` — делать нечего (номер подключён не клиникой, источник не
        настроен или всё уже есть в каталоге), либо Meta не ответила и проход
        повторит монитор. Число, в том числе ``0``, — WABA клиники спрошена и
        локальный каталог стоит сверить: копии появились или нашлись там без нас.
        """
        token = self._own_token(binding)
        if token is None or binding.waba_id is None:
            return None
        source = await self._source_templates()
        if not source:
            return None
        have = await self._repository.template_keys(binding.clinic_id)
        missing = [t for t in source if (t.name, t.language) not in have]
        if not missing:
            return None
        try:
            remote = await self._onboarding.template_definitions(binding.waba_id, token)
        except MetaOnboardingError as exc:
            logger.warning(
                "wa_template_copy_check_failed",
                clinic_id=str(binding.clinic_id),
                status=exc.status,
                meta_code=exc.meta_code,
            )
            return None
        present = {(t.name, t.language) for t in remote}
        created = 0
        for template in missing:
            if (template.name, template.language) in present:
                continue
            try:
                status = await self._onboarding.create_template(binding.waba_id, token, template)
            except MetaOnboardingError as exc:
                # Отказ по одному шаблону не мешает остальным; не созданный
                # шаблон монитор попробует снова через час.
                logger.warning(
                    "wa_template_copy_failed",
                    clinic_id=str(binding.clinic_id),
                    template=template.name,
                    lang=template.language,
                    status=exc.status,
                    meta_code=exc.meta_code,
                    error=str(exc)[:300],
                )
                continue
            created += 1
            logger.info(
                "wa_template_copied",
                clinic_id=str(binding.clinic_id),
                template=template.name,
                lang=template.language,
                meta_status=status,
            )
        return created

    @staticmethod
    def _own_token(binding: ClinicWhatsApp) -> str | None:
        """Токен клиники, если номер в её собственном WABA и копировать есть откуда."""
        source_waba = settings.wa_template_source_waba_id.strip()
        if (
            not binding.active
            or binding.connected_via != SELF_SERVICE
            or not source_waba
            or not settings.wa_cloud_access_token
            or binding.waba_id == source_waba
        ):
            return None
        return binding_token(binding)

    async def _source_templates(self) -> list[TemplateDefinition]:
        """Одобренные шаблоны напоминаний в WABA Skrezo (один запрос на проход)."""
        if self._source is not None:
            return self._source
        names = copied_template_names()
        try:
            rows = await self._onboarding.template_definitions(
                settings.wa_template_source_waba_id.strip(), settings.wa_cloud_access_token
            )
        except MetaOnboardingError as exc:
            # Пустой список до конца прохода: не спрашивать источник на каждом номере.
            logger.warning(
                "wa_template_source_unavailable", status=exc.status, meta_code=exc.meta_code
            )
            self._source = []
            return self._source
        self._source = [t for t in rows if t.name in names and t.status == "APPROVED"]
        absent = names - {t.name for t in self._source}
        if absent:
            logger.warning("wa_template_source_incomplete", absent=sorted(absent))
        return self._source
