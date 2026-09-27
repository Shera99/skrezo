"""Use-case: название и подписи переменных общего шаблона платформы (админка).

Meta отдаёт лишь техническое имя и плейсхолдеры ``{{1}}``, ``{{2}}``; понятные
слова для кабинетов клиник пишет команда Skrezo (решение 5 от 26.09). Шаблоны
самих клиник не правятся — у них показывается имя из Meta.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.models.whatsapp_template import WhatsAppTemplate


class SetTemplateLabelsUseCase:
    def __init__(self, session: AsyncSession, repository: WhatsAppRepository) -> None:
        self._session = session
        self._repository = repository

    async def execute(
        self,
        template_id: uuid.UUID,
        *,
        title: str | None,
        variable_labels: list[str] | None,
    ) -> WhatsAppTemplate:
        """Записать название и/или подписи; ``None`` в подписях — не менять.

        Подписей должно быть ровно столько, сколько переменных у шаблона:
        иначе интерфейс подписал бы не ту переменную (422
        ``labels_count_mismatch``). Пустое название возвращает имя из Meta.
        """
        template = await self._repository.common_template(template_id)
        if template is None:
            raise NotFoundError("Общий шаблон не найден", code="template_not_found")
        if variable_labels is not None:
            expected = len(template.variables)
            if len(variable_labels) != expected:
                raise ValidationAppError(
                    f"Шаблон ждёт {expected} подписей, передано {len(variable_labels)}",
                    code="labels_count_mismatch",
                    extra={"expected": expected},
                )
            template.variable_labels = [label.strip() for label in variable_labels]
        template.title = title.strip() if title and title.strip() else None
        await self._session.commit()
        await self._session.refresh(template)
        return template
