"""Use-case: заявка с лендинга без OTP — вторая дверь единой очереди заявок.

Лид ложится в ту же ``clinic_application``, что и заявка через OTP, но с пометкой
``phone_verified = false``: по неподтверждённому номеру нельзя отправлять доступы,
и менеджер видит это до звонка.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.modules.clinic_applications.application.services.team_notification_service import (
    TeamNotificationService,
)
from app.modules.clinic_applications.domain.rules import parse_doctors_count_range
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.shared.db.models.clinic_application import PENDING_LEAD_INDEX_NAME, ClinicApplication
from app.shared.domain.value_objects import normalize_phone


def _already_submitted(created_at: datetime) -> ConflictError:
    return ConflictError(
        "Заявка по этому номеру уже принята, мы скоро свяжемся",
        code="already_submitted",
        extra={"createdAt": created_at.isoformat()},
    )


class SubmitLeadUseCase:
    def __init__(
        self,
        session: AsyncSession,
        applications: ClinicApplicationRepository,
        notifier: TeamNotificationService,
    ) -> None:
        self._session = session
        self._applications = applications
        self._notifier = notifier

    async def execute(
        self,
        *,
        full_name: str,
        phone_raw: str,
        company_name: str | None,
        doctors_count_range: str | None,
        comment: str | None,
        source: str | None,
    ) -> ClinicApplication:
        """Принять лид или объяснить, почему не нужен (409).

        Дедуп по нормализованному телефону, строго в таком порядке:

        1. по номеру уже есть аккаунт → ``already_client`` (форма предложит войти);
        2. по номеру есть открытая (``pending``) заявка — любой двери →
           ``already_submitted`` с временем её создания (форма скажет «мы уже
           получили заявку»);
        3. иначе заявка создаётся.

        Разобранные (``reviewed``/``rejected``) заявки дедуп не блокируют. От
        гонки двух одновременных отправок защищает частичный уникальный индекс:
        проигравший получает тот же ``already_submitted``.
        """
        phone = normalize_phone(phone_raw)

        if await self._applications.account_exists_by_phone(phone):
            # Ни имени, ни клиники не раскрываем — только факт аккаунта.
            raise ConflictError(
                "По этому номеру уже есть аккаунт — войдите в приложение",
                code="already_client",
            )

        pending_since = await self._applications.last_pending_created_at(phone)
        if pending_since is not None:
            raise _already_submitted(pending_since)

        try:
            application = await self._applications.create(
                phone=phone,
                phone_verified=False,
                full_name=full_name,
                company_name=company_name,
                doctors_count=parse_doctors_count_range(doctors_count_range),
                comment=comment,
                source=source,
            )
        except IntegrityError as exc:
            await self._session.rollback()
            if PENDING_LEAD_INDEX_NAME not in str(exc.orig):
                raise
            # Параллельный запрос успел раньше — отвечаем так же, как на повтор.
            pending_since = await self._applications.last_pending_created_at(phone)
            if pending_since is None:
                raise
            raise _already_submitted(pending_since) from exc

        await self._session.commit()
        # Карточке нужен тот же вид заявки, что и админке (с названием города), —
        # перечитываем строку уже после коммита.
        row = await self._applications.get_row(application.id)
        if row is not None:
            await self._notifier.notify_new_application(row)
        return application
