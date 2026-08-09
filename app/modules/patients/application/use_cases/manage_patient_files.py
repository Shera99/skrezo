"""Use-case'ы файлов пациента: загрузка, список, выдача, удаление.

Доступ везде один и тот же — через :func:`ensure_clinic_patient`: клиника видит
файлы тех пациентов, которых лечит, и чужой пациент отвечает 404, как
несуществующий. Отдельной проверки «файл наш» не нужно: файл принадлежит
пациенту, а не клинике.

Файл на диск кладётся ДО записи в БД, потому что иначе нечего записывать: путь
известен только после сохранения. Если запись не сложится, файл останется
сиротой — поэтому при ошибке коммита он удаляется здесь же.
"""

from __future__ import annotations

import uuid

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.patients.application.services.access import ensure_clinic_patient
from app.modules.patients.infrastructure.repositories.patient_file_repository import (
    PatientFileRepository,
)
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.patient_file import PatientFile
from app.shared.storage.files import resolve_stored_path, save_upload


class UploadPatientFileUseCase:
    def __init__(
        self,
        session: AsyncSession,
        patients: PatientRepository,
        files: PatientFileRepository,
    ) -> None:
        self._session = session
        self._patients = patients
        self._files = files

    async def execute(
        self,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        patient_id: uuid.UUID,
        upload: UploadFile,
        *,
        appointment_id: uuid.UUID | None,
        comment: str | None,
    ) -> PatientFile:
        """Сохранить снимок или документ в карту пациента."""
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        if appointment_id is not None and not await self._files.appointment_belongs(
            clinic_id, patient_id, appointment_id
        ):
            raise ValidationAppError("Приём не найден", code="appointment_not_found")

        stored = await save_upload(upload)
        try:
            record = await self._files.add(
                patient_id=patient_id,
                clinic_id=clinic_id,
                appointment_id=appointment_id,
                kind=stored.kind,
                original_name=upload.filename or f"file.{stored.mime.split('/')[-1]}",
                stored_path=stored.relative_path,
                mime=stored.mime,
                size_bytes=stored.size_bytes,
                comment=comment,
                uploaded_by_membership_id=await self._files.membership_id(account_id, clinic_id),
            )
            await self._session.commit()
        except Exception:
            # Записи о файле не будет — значит и файл никому не принадлежит.
            resolve_stored_path(stored.relative_path).unlink(missing_ok=True)
            raise
        return record


class ListPatientFilesUseCase:
    def __init__(self, patients: PatientRepository, files: PatientFileRepository) -> None:
        self._patients = patients
        self._files = files

    async def execute(
        self,
        clinic_id: uuid.UUID,
        patient_id: uuid.UUID,
        *,
        appointment_id: uuid.UUID | None = None,
    ) -> list[PatientFile]:
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        return await self._files.list_for_patient(patient_id, appointment_id=appointment_id)


class GetPatientFileUseCase:
    def __init__(self, patients: PatientRepository, files: PatientFileRepository) -> None:
        self._patients = patients
        self._files = files

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, file_id: uuid.UUID
    ) -> PatientFile:
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        record = await self._files.get(patient_id, file_id)
        if record is None:
            raise NotFoundError("Файл не найден", code="file_not_found")
        return record


class DeletePatientFileUseCase:
    def __init__(
        self,
        session: AsyncSession,
        patients: PatientRepository,
        files: PatientFileRepository,
    ) -> None:
        self._session = session
        self._patients = patients
        self._files = files

    async def execute(
        self, clinic_id: uuid.UUID, patient_id: uuid.UUID, file_id: uuid.UUID
    ) -> None:
        """Пометить файл удалённым; сам файл на диске остаётся.

        Снимок — клинические данные: «случайно удалил» не должно означать
        «потеряли навсегда». Физическую уборку делает отдельная задача.
        """
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        record = await self._files.get(patient_id, file_id)
        if record is None:
            raise NotFoundError("Файл не найден", code="file_not_found")
        await self._files.soft_delete(record)
        await self._session.commit()
