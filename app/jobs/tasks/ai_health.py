"""Cron §13.5: раз в 5 минут проверяем, жив ли контур ассистента.

Отдельная задача, а не часть разбора вебхука: тихий отказ тем и опасен, что
входящих в этот момент может не быть вовсе — проверять нужно по расписанию.
Обход номеров WhatsApp (вызов Graph API на каждую привязку) вынесен в своё
задание раз в час (Ф8): каждые 5 минут это десятки тысяч вызовов в сутки.
"""

from __future__ import annotations

from typing import Any

from app.core.db import session_factory
from app.jobs.tasks.context import attempt, notifier
from app.modules.ai.application.health import AiHealthService
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.whatsapp.application.services.connection_monitor import (
    WhatsAppConnectionMonitor,
)
from app.shared.ai.factory import build_ai_provider
from app.shared.speech import build_speech_provider
from app.shared.whatsapp_cloud import build_onboarding_client, build_whatsapp_client


async def check_ai_health(ctx: dict[str, Any]) -> int:
    """Модель, распознавание, молчащие клиники, деградация; вернуть число проблем."""
    async with JobRunRecorder("check_ai_health", attempt(ctx)) as run:
        async with session_factory() as session:
            report = await AiHealthService(
                session,
                build_ai_provider(),
                build_whatsapp_client(),
                build_speech_provider(),
            ).check(tokens=False)
        problems = (0 if report.model_ok else 1) + (1 if report.speech_ok is False else 0)
        run.note(
            f"модель: {'ок' if report.model_ok else 'НЕДОСТУПНА'}, "
            f"переведено в assisted: {report.downgraded}, "
            f"молчащих клиник: {len(report.silent_clinics)}"
        )
        return problems


async def check_whatsapp_tokens(ctx: dict[str, Any]) -> int:
    """Раз в час: принимает ли Graph API токен каждого номера; число отказов.

    Номер клиники проверяется её собственным токеном (Embedded Signup). По
    итогам монитор помечает «нужно переподключить», пишет команде Skrezo и
    дозапрашивает контакты и историю приложения, пока открыто окно Meta.
    """
    async with JobRunRecorder("check_whatsapp_tokens", attempt(ctx)) as run:
        async with session_factory() as session:
            failed = await AiHealthService(session, None, build_whatsapp_client()).check_tokens()
            report = await WhatsAppConnectionMonitor(
                session, build_onboarding_client(), notifier(ctx)
            ).run(failed)
        run.note(
            f"номеров с ошибкой токена: {len(failed)}, "
            f"нужно переподключить: +{report.flagged}, восстановилось: {report.cleared}, "
            f"сообщено команде: {report.notified}, запрошено синхронизаций: {report.syncs}"
        )
        return len(failed)
