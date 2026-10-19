"""Use-case: заранее заполнить значения переменных шаблона для конкретного диалога.

Meta значения ``{{1}}``, ``{{2}}`` сама не подставляет — их передаёт
отправитель. Раньше кабинет спрашивал их у сотрудника каждый раз («{1}»,
«{2}»), хотя имя пациента, название клиники и время ближайшей записи бэк
знает (решение 19.10.2026). Здесь по источникам переменных
(:mod:`app.modules.whatsapp.domain.template_sources`) собираются значения;
кабинет подставляет их в поля, а сотрудник проверяет и при желании правит.

Значения нет (нет карточки пациента, нет будущей записи, нет долга, источник
«вручную») — ``null``: поле остаётся пустым, его заполняет сотрудник.
Отправка не меняется — она по-прежнему принимает значения от кабинета.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.cash.infrastructure.repositories.cash_repository import CashRepository
from app.modules.push.domain.events import format_som
from app.modules.whatsapp.domain.template_sources import VariableSource, normalize_sources
from app.modules.whatsapp.domain.templates import template_title, variable_labels
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.patient import Patient

#: Источники, для которых нужна ближайшая запись пациента.
_VISIT_SOURCES = frozenset(
    {
        VariableSource.DOCTOR_NAME,
        VariableSource.APPOINTMENT_DATE,
        VariableSource.APPOINTMENT_TIME,
    }
)


@dataclass(frozen=True, slots=True)
class PrefilledVariable:
    """Переменная шаблона с подписью, источником и найденным значением."""

    placeholder: str
    label: str
    source: VariableSource
    value: str | None


@dataclass(frozen=True, slots=True)
class TemplatePrefill:
    template_id: uuid.UUID
    title: str
    variables: list[PrefilledVariable]

    @property
    def complete(self) -> bool:
        """Все значения найдены — шаблон можно отправить без ввода."""
        return all(v.value for v in self.variables)


class PrefillTemplateUseCase:
    def __init__(self, session: AsyncSession, repository: WhatsAppRepository) -> None:
        self._session = session
        self._repository = repository

    async def execute(
        self,
        clinic_id: uuid.UUID,
        conversation_id: uuid.UUID,
        template_id: uuid.UUID,
        *,
        lang: Lang = Lang.RU,
    ) -> TemplatePrefill:
        """Значения переменных шаблона для пациента этого диалога."""
        conversation = await self._repository.conversation(clinic_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        template = await self._repository.template(clinic_id, template_id)
        if template is None:
            raise NotFoundError("Шаблон не найден", code="template_not_found")

        sources = normalize_sources(template.variables, template.variable_sources)
        labels = variable_labels(
            template.variables, template.variable_labels, template.variable_sources, lang
        )
        values = await self._values(clinic_id, conversation, set(sources))
        return TemplatePrefill(
            template_id=template.id,
            title=template_title(template.meta_name, template.title),
            variables=[
                PrefilledVariable(
                    placeholder=f"{{{{{variable}}}}}",
                    label=labels[i],
                    source=sources[i],
                    value=values.get(sources[i]),
                )
                for i, variable in enumerate(template.variables)
            ],
        )

    async def _values(
        self,
        clinic_id: uuid.UUID,
        conversation: AiConversation,
        needed: set[VariableSource],
    ) -> dict[VariableSource, str]:
        """Значения только нужных источников: запись и долг ищутся, если они в шаблоне."""
        values: dict[VariableSource, str] = {}
        clinic = await self._session.get(Clinic, clinic_id)
        if clinic is not None:
            values.update(
                {
                    source: text.strip()
                    for source, text in (
                        (VariableSource.CLINIC_NAME, clinic.name),
                        (VariableSource.CLINIC_PHONE, clinic.business_phone),
                        (VariableSource.CLINIC_ADDRESS, clinic.address),
                    )
                    if text and text.strip()
                }
            )
        patient_id = conversation.patient_id
        if patient_id is None:
            return values  # без карточки пациента имени, записи и долга нет
        patient = await self._session.get(Patient, patient_id)
        if patient is not None:
            first = (patient.first_name or "").strip()
            full = " ".join(part for part in (first, (patient.last_name or "").strip()) if part)
            if first:
                values[VariableSource.PATIENT_NAME] = first
            if full:
                values[VariableSource.PATIENT_FULL_NAME] = full
        if needed & _VISIT_SOURCES:
            visit = await self._repository.next_visit(clinic_id, patient_id)
            if visit is not None:
                starts_at, doctor = visit
                local = starts_at.astimezone(await self._repository.clinic_tz(clinic_id))
                values[VariableSource.APPOINTMENT_DATE] = local.strftime("%d.%m.%Y")
                values[VariableSource.APPOINTMENT_TIME] = local.strftime("%H:%M")
                if doctor:
                    values[VariableSource.DOCTOR_NAME] = doctor
        if VariableSource.DEBT_AMOUNT in needed:
            debts = await CashRepository(self._session).list_debts(
                clinic_id, patient_id=patient_id, own_membership_id=None
            )
            total = sum(debt.outstanding_tiyin for debt in debts)
            if total > 0:
                # Долга нет — значения нет: «у вас долг 0 сом» пациенту не шлют.
                values[VariableSource.DEBT_AMOUNT] = format_som(total)
        return values
