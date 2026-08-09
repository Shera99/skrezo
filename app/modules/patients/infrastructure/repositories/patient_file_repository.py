"""Репозиторий файлов пациента (метаданные; сам файл — на диске)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import FileKind
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient_file import PatientFile


class PatientFileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        patient_id: uuid.UUID,
        clinic_id: uuid.UUID,
        appointment_id: uuid.UUID | None,
        kind: FileKind,
        original_name: str,
        stored_path: str,
        mime: str,
        size_bytes: int,
        comment: str | None,
        uploaded_by_membership_id: uuid.UUID | None,
    ) -> PatientFile:
        record = PatientFile(
            patient_id=patient_id,
            clinic_id=clinic_id,
            appointment_id=appointment_id,
            kind=kind,
            original_name=original_name,
            stored_path=stored_path,
            mime=mime,
            size_bytes=size_bytes,
            comment=comment,
            uploaded_by_membership_id=uploaded_by_membership_id,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_for_patient(
        self, patient_id: uuid.UUID, *, appointment_id: uuid.UUID | None = None
    ) -> list[PatientFile]:
        """Файлы пациента (свежие сверху), кроме удалённых.

        Клиника-загрузчик в фильтре не участвует: пациент глобальный, и карта у
        него одна на всю платформу — как и сквозная история зуба.
        """
        query = (
            select(PatientFile)
            .where(PatientFile.patient_id == patient_id, PatientFile.deleted_at.is_(None))
            .order_by(desc(PatientFile.created_at))
        )
        if appointment_id is not None:
            query = query.where(PatientFile.appointment_id == appointment_id)
        return list((await self._session.execute(query)).scalars())

    async def get(self, patient_id: uuid.UUID, file_id: uuid.UUID) -> PatientFile | None:
        """Файл пациента; удалённый не отдаём."""
        return (
            await self._session.execute(
                select(PatientFile).where(
                    PatientFile.id == file_id,
                    PatientFile.patient_id == patient_id,
                    PatientFile.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def soft_delete(self, record: PatientFile) -> None:
        """Пометить файл удалённым; на диске он остаётся."""
        record.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def appointment_belongs(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, appointment_id: uuid.UUID
    ) -> bool:
        """Приём этой клиники и этого пациента — иначе файл привязывать нельзя."""
        return (
            await self._session.execute(
                select(Appointment.id).where(
                    Appointment.id == appointment_id,
                    Appointment.clinic_id == clinic_id,
                    Appointment.patient_id == patient_id,
                )
            )
        ).scalar_one_or_none() is not None

    async def membership_id(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Членство загрузившего в этой клинике — «кто загрузил» в карточке файла."""
        return (
            await self._session.execute(
                select(Membership.id).where(
                    Membership.account_id == account_id,
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                )
            )
        ).scalar_one_or_none()
