"""Зависимости модуля пациентов."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.patients.application.use_cases.create_patient import CreatePatientUseCase
from app.modules.patients.application.use_cases.get_patient import GetPatientUseCase
from app.modules.patients.application.use_cases.list_patients import ListPatientsUseCase
from app.modules.patients.application.use_cases.lookup_patient import LookupPatientUseCase
from app.modules.patients.application.use_cases.manage_patient_files import (
    DeletePatientFileUseCase,
    GetPatientFileUseCase,
    ListPatientFilesUseCase,
    UpdatePatientFileUseCase,
    UploadPatientFileUseCase,
)
from app.modules.patients.application.use_cases.tooth_history import ToothHistoryUseCase
from app.modules.patients.application.use_cases.update_patient import UpdatePatientUseCase
from app.modules.patients.application.use_cases.upsert_teeth import UpsertTeethUseCase
from app.modules.patients.infrastructure.repositories.patient_file_repository import (
    PatientFileRepository,
)
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository


def get_patient_repository(session: AsyncSession = Depends(get_session)) -> PatientRepository:
    return PatientRepository(session)


def get_patient_file_repository(
    session: AsyncSession = Depends(get_session),
) -> PatientFileRepository:
    return PatientFileRepository(session)


def get_list_patients_use_case(
    patients: PatientRepository = Depends(get_patient_repository),
) -> ListPatientsUseCase:
    return ListPatientsUseCase(patients)


def get_lookup_patient_use_case(
    patients: PatientRepository = Depends(get_patient_repository),
) -> LookupPatientUseCase:
    return LookupPatientUseCase(patients)


def get_create_patient_use_case(
    session: AsyncSession = Depends(get_session),
    patients: PatientRepository = Depends(get_patient_repository),
) -> CreatePatientUseCase:
    return CreatePatientUseCase(session, patients)


def get_get_patient_use_case(
    patients: PatientRepository = Depends(get_patient_repository),
    files: PatientFileRepository = Depends(get_patient_file_repository),
) -> GetPatientUseCase:
    return GetPatientUseCase(patients, files)


def get_update_patient_use_case(
    session: AsyncSession = Depends(get_session),
    patients: PatientRepository = Depends(get_patient_repository),
) -> UpdatePatientUseCase:
    return UpdatePatientUseCase(session, patients)


def get_upsert_teeth_use_case(
    session: AsyncSession = Depends(get_session),
    patients: PatientRepository = Depends(get_patient_repository),
) -> UpsertTeethUseCase:
    return UpsertTeethUseCase(session, patients)


def get_tooth_history_use_case(
    patients: PatientRepository = Depends(get_patient_repository),
) -> ToothHistoryUseCase:
    return ToothHistoryUseCase(patients)


def get_upload_patient_file_use_case(
    session: AsyncSession = Depends(get_session),
    patients: PatientRepository = Depends(get_patient_repository),
    files: PatientFileRepository = Depends(get_patient_file_repository),
) -> UploadPatientFileUseCase:
    return UploadPatientFileUseCase(session, patients, files)


def get_list_patient_files_use_case(
    patients: PatientRepository = Depends(get_patient_repository),
    files: PatientFileRepository = Depends(get_patient_file_repository),
) -> ListPatientFilesUseCase:
    return ListPatientFilesUseCase(patients, files)


def get_get_patient_file_use_case(
    patients: PatientRepository = Depends(get_patient_repository),
    files: PatientFileRepository = Depends(get_patient_file_repository),
) -> GetPatientFileUseCase:
    return GetPatientFileUseCase(patients, files)


def get_update_patient_file_use_case(
    session: AsyncSession = Depends(get_session),
    patients: PatientRepository = Depends(get_patient_repository),
    files: PatientFileRepository = Depends(get_patient_file_repository),
) -> UpdatePatientFileUseCase:
    return UpdatePatientFileUseCase(session, patients, files)


def get_delete_patient_file_use_case(
    session: AsyncSession = Depends(get_session),
    patients: PatientRepository = Depends(get_patient_repository),
    files: PatientFileRepository = Depends(get_patient_file_repository),
) -> DeletePatientFileUseCase:
    return DeletePatientFileUseCase(session, patients, files)
