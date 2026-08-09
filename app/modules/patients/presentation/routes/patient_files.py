"""Роуты файлов пациента: загрузка, список, выдача и удаление.

Файлы не раздаются статикой: каждый скачивается через ручку, которая проверяет,
лечит ли клиника этого пациента. Иначе угаданный путь в хранилище = чужой
снимок, а снимок — это персональные данные.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.patients.application.use_cases.manage_patient_files import (
    DeletePatientFileUseCase,
    GetPatientFileUseCase,
    ListPatientFilesUseCase,
    UploadPatientFileUseCase,
)
from app.modules.patients.presentation.deps import (
    get_delete_patient_file_use_case,
    get_get_patient_file_use_case,
    get_list_patient_files_use_case,
    get_upload_patient_file_use_case,
)
from app.modules.patients.presentation.schemas import (
    FileDeletedResponse,
    PatientFileOut,
    PatientFilesResponse,
)
from app.shared.auth import ClientContext
from app.shared.storage.files import resolve_stored_path

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Пациент этой клиники не найден либо файла нет",
        "content": {
            "application/json": {
                "example": {"error": "patient_not_found", "message": "Пациент не найден"}
            }
        },
    },
}

_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    **_NOT_FOUND,
    422: {
        "description": "Неподдерживаемый тип, пустой или слишком большой файл",
        "content": {
            "application/json": {
                "example": {
                    "error": "unsupported_file_type",
                    "message": (
                        "Такой тип файла не поддерживается: "
                        "только JPEG, PNG, WebP, HEIC и PDF"
                    ),
                }
            }
        },
    },
}


@router.post(
    "/clinic/patients/{patient_id}/files",
    response_model=PatientFileOut,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить файл в карту пациента",
    response_description="Метаданные сохранённого файла",
    responses=_UPLOAD_ERRORS,
)
async def upload_patient_file(
    patient_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="Снимок или документ")],
    appointment_id: Annotated[
        uuid.UUID | None, Form(alias="appointmentId", description="Приём, к которому относится")
    ] = None,
    comment: Annotated[str | None, Form(max_length=500)] = None,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: UploadPatientFileUseCase = Depends(get_upload_patient_file_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> PatientFileOut:
    """Снимок или документ в карту пациента (multipart).

    Тип определяется **по содержимому**, а не по заголовку запроса: клиент
    называет тип сам, и `image/jpeg` с чем угодно внутри — это то, что потом
    скачает сотрудник. Поддерживаются JPEG, PNG, WebP, HEIC и PDF.

    Имя файла на диске генерируем мы (uuid + расширение); присланное имя
    хранится отдельно и только для показа — иначе `../` в имени уводит запись за
    пределы хранилища.

    ⚠️ Пациент **глобальный**, поэтому файл увидят все клиники, которые его
    лечат, — как и сквозную историю зуба. Кто загрузил, видно в `clinicId`.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    record = await use_case.execute(
        context.clinic_id,
        context.account_id,
        patient_id,
        file,
        appointment_id=appointment_id,
        comment=comment,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="patient.file_upload",
        entity_type="patient",
        entity_id=patient_id,
        payload={"fileId": str(record.id), "kind": record.kind.value},
    )
    return PatientFileOut.from_record(record)


@router.get(
    "/clinic/patients/{patient_id}/files",
    response_model=PatientFilesResponse,
    summary="Файлы пациента",
    response_description="Метаданные файлов (свежие сверху)",
    responses=_NOT_FOUND,
)
async def list_patient_files(
    patient_id: uuid.UUID,
    appointment_id: uuid.UUID | None = Query(default=None, alias="appointmentId"),
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: ListPatientFilesUseCase = Depends(get_list_patient_files_use_case),
) -> PatientFilesResponse:
    """Список файлов карты; ``appointmentId`` сужает до одного приёма."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    records = await use_case.execute(
        context.clinic_id, patient_id, appointment_id=appointment_id
    )
    return PatientFilesResponse(items=[PatientFileOut.from_record(r) for r in records])


@router.get(
    "/clinic/patients/{patient_id}/files/{file_id}",
    summary="Скачать файл пациента",
    response_description="Содержимое файла",
    responses=_NOT_FOUND,
)
async def download_patient_file(
    patient_id: uuid.UUID,
    file_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: GetPatientFileUseCase = Depends(get_get_patient_file_use_case),
) -> FileResponse:
    """Отдать содержимое файла.

    Заголовок ``Content-Disposition: attachment`` и запрет угадывания типа
    (``nosniff``) стоят намеренно: файл открывается на нашем домене, где у
    сотрудника живая сессия, и показывать его инлайн — лишний риск.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    record = await use_case.execute(context.clinic_id, patient_id, file_id)
    return FileResponse(
        resolve_stored_path(record.stored_path),
        media_type=record.mime,
        filename=record.original_name,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/clinic/patients/{patient_id}/files/{file_id}",
    response_model=FileDeletedResponse,
    summary="Удалить файл пациента",
    response_description="Файл помечен удалённым",
    responses=_NOT_FOUND,
)
async def delete_patient_file(
    patient_id: uuid.UUID,
    file_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: DeletePatientFileUseCase = Depends(get_delete_patient_file_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> FileDeletedResponse:
    """Убрать файл из карты.

    Удаление мягкое: запись помечается, файл на диске остаётся. Снимок — это
    клинические данные, и «случайно удалил» не должно означать «потеряли
    навсегда».
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    await use_case.execute(context.clinic_id, patient_id, file_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="patient.file_delete",
        entity_type="patient",
        entity_id=patient_id,
        payload={"fileId": str(file_id)},
    )
    return FileDeletedResponse(id=file_id)
