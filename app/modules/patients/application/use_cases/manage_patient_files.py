"""Use-case'ы файлов пациента: загрузка, список, выдача, правка, удаление.

Доступ к чтению везде один и тот же — через :func:`ensure_clinic_patient`:
клиника видит файлы тех пациентов, которых лечит, и чужой пациент отвечает 404,
как несуществующий. Файл принадлежит пациенту, поэтому видят и скачивают его
все клиники пациента.

Менять файл — другое дело: описание, зубы и само существование файла правит
только клиника, которая его загрузила. Иначе любая клиника, к которой пациент
однажды зашёл, может стереть чужую КТ — а для той клиники это потеря данных.
Чужой файл на правку отвечает 403, а не 404: файл клиника видит, и «не найден»
был бы неправдой.

Файл на диск кладётся ДО записи в БД, потому что иначе нечего записывать: путь
известен только после сохранения. Если запись не сложится, файл останется
сиротой — поэтому при ошибке коммита он удаляется здесь же.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.modules.patients.application.services.access import ensure_clinic_patient
from app.modules.patients.domain.models import FileSource, PatientFileView
from app.modules.patients.domain.teeth import is_valid_tooth
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
        teeth: list[int] | None = None,
        taken_date: date | None = None,
    ) -> PatientFileView:
        """Сохранить снимок, документ или КТ в карту пациента (или к его зубам)."""
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        if appointment_id is not None and not await self._files.appointment_belongs(
            clinic_id, patient_id, appointment_id
        ):
            raise ValidationAppError("Приём не найден", code="appointment_not_found")
        # Зубы и дату проверяем ДО записи в хранилище: иначе сотни мегабайт КТ
        # скопируются туда только ради того, чтобы получить 422 за опечатку в
        # номере. (Само тело к этому моменту уже принято — FastAPI разбирает
        # форму раньше, чем вызывает ручку.)
        tooth_numbers = normalize_teeth(teeth or [])
        ensure_taken_date(taken_date)

        stored = await save_upload(upload)
        try:
            record = await self._files.add(
                patient_id=patient_id,
                clinic_id=clinic_id,
                appointment_id=appointment_id,
                kind=stored.kind,
                tooth_numbers=tooth_numbers,
                taken_date=taken_date,
                original_name=upload.filename or f"file.{stored.mime.split('/')[-1]}",
                stored_path=stored.relative_path,
                mime=stored.mime,
                size_bytes=stored.size_bytes,
                comment=comment,
                uploaded_by_membership_id=await self._files.membership_id(account_id, clinic_id),
            )
            clinic_name = await self._files.clinic_name(clinic_id)
            await self._session.commit()
        except Exception:
            # Записи о файле не будет — значит и файл никому не принадлежит.
            resolve_stored_path(stored.relative_path).unlink(missing_ok=True)
            raise
        return PatientFileView(record=record, clinic_name=clinic_name)


class ListPatientFilesUseCase:
    def __init__(self, patients: PatientRepository, files: PatientFileRepository) -> None:
        self._patients = patients
        self._files = files

    async def execute(
        self,
        clinic_id: uuid.UUID,
        patient_id: uuid.UUID,
        *,
        source: FileSource = FileSource.OWN,
        tooth: int | None = None,
        general: bool = False,
        appointment_id: uuid.UUID | None = None,
    ) -> tuple[list[PatientFileView], int]:
        """Файлы под отбором и сколько под тем же отбором файлов других клиник.

        Второе число — для подсказки «есть N файлов из других клиник», когда
        показаны только свои: без него приложению пришлось бы тянуть чужие
        файлы целиком ради одной цифры.
        """
        if tooth is not None and not is_valid_tooth(tooth):
            raise ValidationAppError("Некорректный номер зуба (FDI)", code="invalid_tooth")
        if tooth is not None and general:
            raise ValidationAppError(
                "Нельзя одновременно просить файлы зуба и только общие",
                code="conflicting_filters",
            )
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        items = await self._files.list_for_patient(
            patient_id,
            clinic_id=clinic_id,
            source=source,
            tooth=tooth,
            general=general,
            appointment_id=appointment_id,
        )
        others = await self._files.count(
            patient_id,
            clinic_id=clinic_id,
            source=FileSource.OTHERS,
            tooth=tooth,
            general=general,
            appointment_id=appointment_id,
        )
        return items, others


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


class UpdatePatientFileUseCase:
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
        patient_id: uuid.UUID,
        file_id: uuid.UUID,
        fields: dict[str, Any],
    ) -> PatientFileView:
        """Поправить описание, зубы или дату снимка своего файла.

        ``fields`` — только присланные поля (``exclude_unset``), под именами
        колонок. Содержимое файла не меняется: другой файл — новая загрузка.
        """
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        record = await self._files.get(patient_id, file_id)
        if record is None:
            raise NotFoundError("Файл не найден", code="file_not_found")
        ensure_own_file(record, clinic_id)
        if "tooth_numbers" in fields:
            fields["tooth_numbers"] = normalize_teeth(fields["tooth_numbers"])
        if "taken_date" in fields:
            ensure_taken_date(fields["taken_date"])
        await self._files.update(record, fields)
        clinic_name = await self._files.clinic_name(clinic_id)
        await self._session.commit()
        return PatientFileView(record=record, clinic_name=clinic_name)


def normalize_teeth(teeth: list[int]) -> list[int]:
    """Номера зубов без повторов и по порядку; неверный номер FDI — 422."""
    invalid = sorted({t for t in teeth if not is_valid_tooth(t)})
    if invalid:
        raise ValidationAppError(
            f"Некорректный номер зуба (FDI): {', '.join(map(str, invalid))}",
            code="invalid_tooth",
        )
    return sorted(set(teeth))


def ensure_taken_date(taken_date: date | None) -> None:
    """Дата снимка не из будущего.

    Сутки запаса — на часовой пояс: у клиники в Бишкеке «сегодня» наступает на
    шесть часов раньше, чем по UTC.
    """
    if taken_date is not None and taken_date > datetime.now(UTC).date() + timedelta(days=1):
        raise ValidationAppError("Дата снимка не может быть в будущем", code="taken_date_in_future")


def ensure_own_file(record: PatientFile, clinic_id: uuid.UUID) -> None:
    """Менять файл может только клиника, которая его загрузила; иначе 403."""
    if record.clinic_id != clinic_id:
        raise ForbiddenError(
            "Файл загружен другой клиникой — изменить или удалить его может только она",
            code="file_not_owned",
        )


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
        Удалить может только клиника, загрузившая файл.
        """
        await ensure_clinic_patient(clinic_id, patient_id, self._patients)
        record = await self._files.get(patient_id, file_id)
        if record is None:
            raise NotFoundError("Файл не найден", code="file_not_found")
        ensure_own_file(record, clinic_id)
        await self._files.soft_delete(record)
        await self._session.commit()
