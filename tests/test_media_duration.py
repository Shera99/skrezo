"""Длительность голосового читается из файла: Meta её в вебхуке не присылает."""

from __future__ import annotations

import io
import struct
import wave

from app.modules.whatsapp.application.services.media import media_duration_seconds


def wav_bytes(seconds: float, rate: int = 8000) -> bytes:
    """Тишина заданной длины в WAV — mutagen читает её как любой аудиофайл."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return buf.getvalue()


def test_duration_is_rounded_to_seconds() -> None:
    assert media_duration_seconds(wav_bytes(1.5)) == 2
    assert media_duration_seconds(wav_bytes(14.2)) == 14


def test_short_clip_never_shows_zero() -> None:
    """Секундное «ага» не должно превращаться в 0:00."""
    assert media_duration_seconds(wav_bytes(0.3)) == 1


def test_garbage_is_not_an_error() -> None:
    assert media_duration_seconds(b"not audio at all") is None
    assert media_duration_seconds(b"") is None
