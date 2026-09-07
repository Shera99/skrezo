"""Сборка провайдера распознавания; без настройки голосовые уходят человеку."""

from __future__ import annotations

from app.core.config import settings
from app.shared.http import shared_async_client
from app.shared.speech.gemini import SPEECH_TIMEOUT, GeminiSpeechProvider
from app.shared.speech.provider import SpeechProvider


def build_speech_provider() -> SpeechProvider | None:
    """Провайдер по конфигу; ``None`` — распознавание выключено.

    Как и у модели, лог-заглушки нет: выдуманная расшифровка опаснее её
    отсутствия. Выключено — голосовое сохраняется и уходит администратору.
    """
    if settings.speech_provider == "gemini" and settings.gemini_api_key:
        return GeminiSpeechProvider(
            settings.gemini_api_key,
            settings.speech_model,
            # Ф8: один пул соединений на процесс; у речи свой таймаут (файлы).
            client=shared_async_client("speech", timeout=SPEECH_TIMEOUT),
        )
    return None
