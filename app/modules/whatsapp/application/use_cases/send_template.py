"""Use-case: отправить одобренный шаблон (работает и в закрытое окно)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationAppError
from app.modules.whatsapp.domain.templates import is_service_template
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiMessageRole
from app.shared.db.models.ai_message import AiMessage
from app.shared.realtime import publish_realtime
from app.shared.whatsapp_cloud import WhatsAppCloudClient, WhatsAppSendError

#: Плейсхолдер тела шаблона Meta: {{1}}, {{2}}, …
_PLACEHOLDER_RE = re.compile(r"\{\{(\d+)\}\}")


def render_template_body(body_preview: str, variables: list[str]) -> str:
    """Подставить переменные в превью — так шаблон читается в истории диалога.

    В WhatsApp текст соберёт сама Meta; рендер здесь — только для нашей
    истории: строка «Напоминаем о приёме {{1}}» без подстановки бесполезна
    администратору, который смотрит, что именно ушло пациенту.
    """

    def _sub(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return variables[index] if 0 <= index < len(variables) else match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, body_preview)


class SendTemplateUseCase:
    def __init__(
        self,
        session: AsyncSession,
        repository: WhatsAppRepository,
        client: WhatsAppCloudClient,
    ) -> None:
        self._session = session
        self._repository = repository
        self._client = client

    async def execute(
        self,
        clinic_id: uuid.UUID,
        conversation_id: uuid.UUID,
        template_id: uuid.UUID,
        variables: list[str],
    ) -> AiMessage:
        """Отправить шаблон в диалог; окно 24 часов НЕ проверяется — в этом смысл.

        Только ``approved``: остальное Graph API всё равно отвергнет, но своим
        кодом ошибки понятнее. Число переменных обязано совпасть с шаблоном —
        Meta на несовпадение отвечает молчаливым отказом доставки.
        """
        conversation = await self._repository.conversation(clinic_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        binding = await self._repository.binding_by_clinic(clinic_id)
        if binding is None or not binding.active:
            raise ConflictError("WhatsApp не подключён к клинике", code="whatsapp_not_connected")
        template = await self._repository.template(clinic_id, template_id)
        if template is None:
            raise NotFoundError("Шаблон не найден", code="template_not_found")
        if is_service_template(template.meta_name, settings.wa_service_template_names):
            # Тестовый hello_world Meta одобрен и utility, но это текст для
            # проверки канала, а не для пациента.
            raise ValidationAppError(
                "Служебный шаблон Meta нужен для проверки канала — пациентам не отправляется",
                code="template_service",
                extra={"metaName": template.meta_name},
            )
        if template.status != "approved":
            raise ValidationAppError(
                "Шаблон не одобрен Meta — отправлять можно только approved",
                code="template_not_approved",
                extra={"status": template.status},
            )
        expected = len(template.variables)
        if len(variables) != expected:
            raise ValidationAppError(
                f"Шаблон ждёт {expected} переменных, передано {len(variables)}",
                code="template_variables_mismatch",
                extra={"expected": expected},
            )
        try:
            sent = await self._client.send_template(
                binding.phone_number_id,
                conversation.wa_phone,
                template.meta_name,
                template.lang,
                variables,
            )
        except WhatsAppSendError as exc:
            raise AppError(
                "Не удалось отправить шаблон в WhatsApp",
                code="wa_send_failed",
                status_code=502,
            ) from exc
        now = datetime.now(UTC)
        message = await self._repository.add_message(
            conversation,
            role=AiMessageRole.ADMIN,
            body=render_template_body(template.body_preview, variables),
            wa_message_id=sent.wa_message_id,
            sent_at=now,
        )
        conversation.last_msg_at = now
        await publish_realtime(
            self._session,
            clinic_id,
            "conversation.message",
            {"conversationId": str(conversation.id), "direction": "out"},
        )
        await self._session.commit()
        return message
