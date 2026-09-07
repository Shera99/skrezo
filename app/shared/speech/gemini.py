"""Распознавание голосовых через Gemini — тем же ключом, что и ассистент.

Отдельный ASR-сервис — открытое решение (см. дорожную карту), и до него
голосовые расшифровывает мультимодальная модель: это не требует нового
вендора, договора и бюджета.

⚠️ Честное ограничение: собственной оценки уверенности у модели нет, она
сообщает её сама по нашей просьбе — такая самооценка слабее, чем у
специализированного ASR. Поэтому порог намеренно высокий, а пустой ответ
трактуется как отказ: правило «низкая уверенность → человек» важнее полноты
автоматизации. Качество на кыргызском проверяется отдельным тестом.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import structlog

from app.shared.speech.provider import SpeechError, SpeechResult

logger = structlog.get_logger(__name__)

_TIMEOUT = 60.0
SPEECH_TIMEOUT = _TIMEOUT

_PROMPT = (
    "Расшифруй голосовое сообщение пациента стоматологической клиники. "
    "Речь может быть на русском, кыргызском или смешанной. Верни СТРОГО один "
    'JSON без пояснений: {"text": "<дословная расшифровка>", "confidence": '
    "<число 0..1 — насколько уверенно ты разобрал речь>}. Если запись "
    'неразборчива или это не речь — верни {"text": "", "confidence": 0}.'
)


class GeminiSpeechProvider:
    """Расшифровка через ``generateContent`` с инлайн-аудио."""

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self.model_name = model
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    def _url(self) -> str:
        return (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent"
        )

    async def transcribe(
        self, audio: bytes, mime: str, lang_hint: str | None = None
    ) -> SpeechResult:
        hint = ""
        if lang_hint:
            hint = f" Ожидаемый язык: {lang_hint}."
        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": _PROMPT + hint},
                        {
                            "inline_data": {
                                "mime_type": mime or "audio/ogg",
                                "data": base64.b64encode(audio).decode(),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 512},
        }
        try:
            resp = await self._client.post(
                self._url(), json=body, headers={"x-goog-api-key": self._api_key}
            )
        except httpx.HTTPError as exc:
            raise SpeechError(f"Gemini ASR: {exc}") from exc
        if resp.status_code >= 400:
            logger.warning("gemini_asr_failed", status=resp.status_code, body=resp.text[:300])
            raise SpeechError(f"Gemini ASR: HTTP {resp.status_code}")
        return _parse(resp.json())


def _parse(data: dict[str, Any]) -> SpeechResult:
    """Достать текст и уверенность; любой сбой разбора — отказ, не догадка."""
    try:
        parts = data["candidates"][0].get("content", {}).get("parts") or []
    except (KeyError, IndexError) as exc:
        raise SpeechError("Gemini ASR: пустой ответ") from exc
    raw = "".join(p.get("text", "") for p in parts).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{") :] if "{" in raw else raw
    try:
        payload = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    except (ValueError, TypeError) as exc:
        raise SpeechError("Gemini ASR: ответ не JSON") from exc
    text = str(payload.get("text") or "").strip()
    try:
        confidence = float(payload.get("confidence") or 0.0)
    except TypeError, ValueError:
        confidence = 0.0
    return SpeechResult(text=text, confidence=max(0.0, min(1.0, confidence)))
