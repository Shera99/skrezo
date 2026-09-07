"""Сборка AI-провайдера из настроек; без ключа ассистент выключен."""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings
from app.shared.ai.gemini import GeminiProvider
from app.shared.ai.provider import AiProvider
from app.shared.ai.resilience import ResilientProvider


def build_ai_provider(redis: Redis | None = None) -> AiProvider | None:
    """Провайдер по конфигу; ``None`` — ассистент не настроен (пайплайн молчит).

    ``None`` вместо лог-фолбэка намеренно: «фейковый ответ пациенту» не бывает
    безвредным, в отличие от пуша в лог. Нет провайдера — диалог просто ждёт
    человека, как до Ф2.

    Провайдер всегда обёрнут в :class:`ResilientProvider` (повторы на 429/5xx).
    ``redis`` включает предохранитель, общий для всех процессов; без него
    (проверка здоровья, тесты) работают только повторы.
    """
    if settings.ai_provider == "gemini" and settings.gemini_api_key:
        inner = GeminiProvider(
            settings.gemini_api_key,
            settings.gemini_model,
            thinking_level=settings.gemini_thinking_level,
            # Ф8: реестр кэшей префикса общий для всех процессов — тот же Redis.
            redis=redis,
            cache_ttl_seconds=settings.ai_prefix_cache_ttl_seconds,
            cache_enabled=settings.ai_prefix_cache_enabled,
        )
        return ResilientProvider(
            inner,
            redis,
            retries=settings.ai_provider_retries,
            backoff_seconds=settings.ai_provider_backoff_seconds,
            breaker_threshold=settings.ai_breaker_threshold,
            breaker_open_seconds=settings.ai_breaker_open_seconds,
        )
    return None
