"""Cron §13.5: раз в 5 минут проверяем, жив ли контур ассистента.

Отдельная задача, а не часть разбора вебхука: тихий отказ тем и опасен, что
входящих в этот момент может не быть вовсе — проверять нужно по расписанию.
Обход номеров WhatsApp (вызов Graph API на каждую привязку) вынесен в своё
задание раз в час (Ф8): каждые 5 минут это десятки тысяч вызовов в сутки.
"""

from __future__ import annotations

from typing import Any

from app.core.db import session_factory
from app.jobs.tasks.context import attempt
from app.modules.ai.application.health import AiHealthService
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.shared.ai.factory import build_ai_provider
from app.shared.speech import build_speech_provider
from app.shared.whatsapp_cloud import build_whatsapp_client


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
    """Раз в час: принимает ли Graph API наш токен по каждому номеру; число отказов."""
    async with JobRunRecorder("check_whatsapp_tokens", attempt(ctx)) as run:
        async with session_factory() as session:
            failed = await AiHealthService(session, None, build_whatsapp_client()).check_tokens()
        run.note(f"номеров с ошибкой токена: {len(failed)}")
        return len(failed)
