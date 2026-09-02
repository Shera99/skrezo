"""Распознавание голосовых сообщений (§11)."""

from app.shared.speech.factory import build_speech_provider
from app.shared.speech.provider import SpeechError, SpeechProvider, SpeechResult

__all__ = [
    "SpeechError",
    "SpeechProvider",
    "SpeechResult",
    "build_speech_provider",
]
