"""Use-case: название, подписи и источники значений переменных шаблона.

Meta отдаёт лишь техническое имя и плейсхолдеры ``{{1}}``, ``{{2}}``; понятные
слова и смысл переменных пишет человек. Общие шаблоны платформы правит
команда Skrezo в админке (решение 5 от 26.09), шаблоны клиники — сама клиника
в кабинете (решение 19.10.2026: без этого кабинет не знал, что ``{{1}}`` —
имя пациента, и не мог подставить его сам).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.modules.whatsapp.domain.template_sources import VariableSource
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
        variable_sources: list[VariableSource] | None = None,
        clinic_id: uuid.UUID | None = None,
    ) -> WhatsAppTemplate:
        """Записать название, подписи и/или источники; ``None`` в списках — не менять.

        ``clinic_id`` — правит клиника: только свой шаблон, общий шаблон
        платформы — 403 ``template_shared`` (его подписывает Skrezo). Без
        ``clinic_id`` — админка Skrezo: только общие шаблоны.

        Подписей и источников должно быть ровно столько, сколько переменных у
        шаблона: иначе интерфейс подписал бы не ту переменную (422
        ``labels_count_mismatch`` / ``sources_count_mismatch``). Пустое
        название возвращает имя из Meta.
        """
        template = await self._template(template_id, clinic_id)
        expected = len(template.variables)
        if variable_labels is not None:
            if len(variable_labels) != expected:
                raise ValidationAppError(
                    f"Шаблон ждёт {expected} подписей, передано {len(variable_labels)}",
                    code="labels_count_mismatch",
                    extra={"expected": expected},
                )
            template.variable_labels = [label.strip() for label in variable_labels]
        if variable_sources is not None:
            if len(variable_sources) != expected:
                raise ValidationAppError(
                    f"Шаблон ждёт {expected} источников, передано {len(variable_sources)}",
                    code="sources_count_mismatch",
                    extra={"expected": expected},
                )
            template.variable_sources = [source.value for source in variable_sources]
        template.title = title.strip() if title and title.strip() else None
        await self._session.commit()
        await self._session.refresh(template)
        return template

    async def _template(
        self, template_id: uuid.UUID, clinic_id: uuid.UUID | None
    ) -> WhatsAppTemplate:
        if clinic_id is None:
            common = await self._repository.common_template(template_id)
            if common is None:
                raise NotFoundError("Общий шаблон не найден", code="template_not_found")
            return common
        template = await self._repository.template(clinic_id, template_id)
        if template is None:
            raise NotFoundError("Шаблон не найден", code="template_not_found")
        if template.clinic_id is None:
            raise ForbiddenError(
                "Общий шаблон платформы подписывает Skrezo — его подписи не меняются",
                code="template_shared",
            )
        return template
