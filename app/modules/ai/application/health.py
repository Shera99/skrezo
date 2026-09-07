"""Мониторинг здоровья интеграции ассистента (§13.5).

Все отказы этой системы **молчаливые**: провайдер модели лёг, токен WhatsApp
истёк, Meta отключила подписку после серии неудачных доставок — ассистент
просто перестаёт работать, ошибка нигде не всплывает, и узнаём мы об этом от
клиники через день. Это худший способ узнать.

Два правила раздела соблюдаются буквально:

1. **Алерты идут нам, а не клинике.** Клиника не может починить наш токен,
   сообщение об этом её только напугает. Ей достаётся деградация поведения:
   при недоступной модели режим ``auto`` автоматически опускается до
   ``assisted`` — ответы уходят через администратора.
2. Проверка дешёвая и частая (раз в 5 минут), поэтому пробный вызов модели
   делается один на всю платформу, а не на каждую клинику.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.ai import AiMessagePart, AiProvider, AiProviderError
from app.shared.db.enums import AiMessageRole, AiMode
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.speech import SpeechError, SpeechProvider
from app.shared.whatsapp_cloud import WhatsAppCloudClient

logger = structlog.get_logger(__name__)

#: Пробный вызов модели: ответ не важен, важен сам факт ответа.
_PROBE_SYSTEM = "Ответь одним словом: ок."
_PROBE_TOKENS = 16

#: Клиника считается живой, если за неделю пришло хотя бы столько сообщений;
#: молчание живой клиники дольше порога — повод заподозрить тихий отказ.
_ACTIVE_MIN_MESSAGES = 10
_SILENCE_ALERT = timedelta(hours=12)
_ACTIVITY_WINDOW = timedelta(days=7)


@dataclass
class HealthReport:
    """Что проверили и что нашли."""

    model_ok: bool = True
    speech_ok: bool | None = None  # None — распознавание не настроено
    tokens_failed: list[str] = field(default_factory=list)
    silent_clinics: list[str] = field(default_factory=list)
    downgraded: int = 0

    @property
    def healthy(self) -> bool:
        return self.model_ok and not self.tokens_failed and self.speech_ok is not False


class AiHealthService:
    """Проверки §13.5 и автоматическая деградация режима."""

    def __init__(
        self,
        session: AsyncSession,
        provider: AiProvider | None,
        wa_client: WhatsAppCloudClient,
        speech: SpeechProvider | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._wa = wa_client
        self._speech = speech

    async def check(self) -> HealthReport:
        report = HealthReport()
        report.model_ok = await self._model_ok()
        if not report.model_ok:
            # Клиника узнаёт о поломке не алертом, а поведением: ответы
            # начинают уходить через администратора (§13.5, правило 1).
            report.downgraded = await self._downgrade_auto_clinics()
        report.speech_ok = await self._speech_ok()
        report.tokens_failed = await self._failed_tokens()
        report.silent_clinics = await self._silent_clinics()

        if not report.healthy:
            logger.error(
                "ai_health_alert",
                model_ok=report.model_ok,
                speech_ok=report.speech_ok,
                tokens_failed=report.tokens_failed,
                downgraded=report.downgraded,
            )
        for clinic_id in report.silent_clinics:
            logger.warning("ai_health_silence", clinic_id=clinic_id)
        return report

    # ── Отдельные проверки ──────────────────────────────────────────────────

    async def _model_ok(self) -> bool:
        if self._provider is None:
            return True  # ассистент не настроен — ломаться нечему
        try:
            result = await self._provider.complete(
                system=_PROBE_SYSTEM,
                parts=[AiMessagePart(role="user", text="ок?")],
                tools=[],
                temperature=0.0,
                max_tokens=_PROBE_TOKENS,
            )
        except AiProviderError as exc:
            logger.warning("ai_health_model_down", error=str(exc))
            return False
        return bool((result.text or "").strip()) or result.tool_call is not None

    async def _speech_ok(self) -> bool | None:
        """Пробная расшифровка. ``None`` — распознавание не настроено.

        Отправляем заведомо пустую запись: нам важна доступность сервиса, а не
        качество — пустой результат при живом сервисе ошибкой не считается.
        """
        if self._speech is None:
            return None
        try:
            await self._speech.transcribe(b"", "audio/ogg")
        except SpeechError as exc:
            logger.warning("ai_health_speech_down", error=str(exc))
            return False
        return True

    async def _failed_tokens(self) -> list[str]:
        """Номера, для которых Graph API больше не принимает наш токен."""
        bindings = (
            await self._session.execute(
                select(ClinicWhatsApp).where(ClinicWhatsApp.active.is_(True))
            )
        ).scalars()
        failed: list[str] = []
        for binding in bindings:
            if not await self._wa.check_token(binding.phone_number_id):
                failed.append(binding.phone_number_id)
        return failed

    async def _silent_clinics(self) -> list[str]:
        """Живые клиники, от которых давно нет входящих, — возможен тихий отказ."""
        now = datetime.now(UTC)
        rows = (
            await self._session.execute(
                select(
                    AiMessage.clinic_id,
                    func.count().label("total"),
                    func.max(AiMessage.created_at).label("last_at"),
                )
                .where(
                    AiMessage.role == AiMessageRole.PATIENT,
                    AiMessage.created_at >= now - _ACTIVITY_WINDOW,
                )
                .group_by(AiMessage.clinic_id)
            )
        ).all()
        return [
            str(clinic_id)
            for clinic_id, total, last_at in rows
            if total >= _ACTIVE_MIN_MESSAGES and last_at < now - _SILENCE_ALERT
        ]

    async def _downgrade_auto_clinics(self) -> int:
        """``auto`` → ``assisted``, пока модель недоступна.

        Возврат наверх — осознанное действие через ручку режима: тихо вернуть
        клинику в ``auto`` после мигнувшего провайдера мы не вправе.
        """
        result = await self._session.execute(
            update(Clinic)
            .where(Clinic.ai_mode == AiMode.AUTO)
            .values(ai_mode=AiMode.ASSISTED, ai_mode_reason="health", ai_mode_changed_at=func.now())
            .returning(Clinic.id)
        )
        changed = list(result.scalars())
        if changed:
            await self._session.commit()
            logger.error("ai_health_downgraded", clinics=[str(c) for c in changed])
        return len(changed)
