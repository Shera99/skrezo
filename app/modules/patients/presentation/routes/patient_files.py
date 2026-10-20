"""Роуты файлов пациента: загрузка, список, выдача, правка и удаление.

Файлы не раздаются статикой: каждый скачивается через ручку, которая проверяет,
лечит ли клиника этого пациента. Иначе угаданный путь в хранилище = чужой
снимок, а снимок — это персональные данные.

Видят файл все клиники пациента, а правят и удаляют — только загрузившая.
"""

from __future__ import annotations

import uuid
from datetime import date
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
    UpdatePatientFileUseCase,
    UploadPatientFileUseCase,
)
from app.modules.patients.domain.models import FileSource
from app.modules.patients.presentation.deps import (
    get_delete_patient_file_use_case,
    get_get_patient_file_use_case,
    get_list_patient_files_use_case,
    get_update_patient_file_use_case,
    get_upload_patient_file_use_case,
)
from app.modules.patients.presentation.schemas import (
    FileDeletedResponse,
    PatientFileOut,
    PatientFilesResponse,
    UpdatePatientFileRequest,
)
from app.shared.auth import ClientContext
from app.shared.storage.files import download_name, resolve_stored_path

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

_INVALID_TOOTH: dict[str, Any] = {
    "error": "invalid_tooth",
    "message": "Некорректный номер зуба (FDI): 19",
}

_FUTURE_DATE: dict[str, Any] = {
    "error": "taken_date_in_future",
    "message": "Дата снимка не может быть в будущем",
}

_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    **_NOT_FOUND,
    422: {
        "description": (
            "Неподдерживаемый тип, пустой или слишком большой файл; архив КТ без DICOM, "
            "с программой внутри, с паролем или повреждённый; неверный номер зуба; "
            "дата снимка из будущего"
        ),
        "content": {
            "application/json": {
                "examples": {
                    "unsupported_file_type": {
                        "value": {
                            "error": "unsupported_file_type",
                            "message": (
                                "Такой тип файла не поддерживается: только JPEG, PNG, "
                                "WebP, HEIC, PDF и КТ (DICOM или ZIP-архив с DICOM)"
                            ),
                        }
                    },
                    "file_too_large": {
                        "value": {"error": "file_too_large", "message": "Файл больше 20 МБ"}
                    },
                    "archive_has_executable": {
                        "value": {
                            "error": "archive_has_executable",
                            "message": (
                                "В архиве есть программа или скрипт — загрузите только "
                                "снимки исследования (без программы просмотра)"
                            ),
                        }
                    },
                    "archive_without_dicom": {
                        "value": {
                            "error": "archive_without_dicom",
                            "message": (
                                "В архиве нет снимков DICOM — для КТ нужен экспорт "
                                "исследования в DICOM"
                            ),
                        }
                    },
                    "archive_unsupported_compression": {
                        "value": {
                            "error": "archive_unsupported_compression",
                            "message": (
                                "Архив сжат неподдерживаемым способом — пересоберите его "
                                "как обычный ZIP"
                            ),
                        }
                    },
                    "archive_too_many_entries": {
                        "value": {
                            "error": "archive_too_many_entries",
                            "message": (
                                "В архиве больше 20000 файлов — это не похоже на одно исследование"
                            ),
                        }
                    },
                    "archive_encrypted": {
                        "value": {
                            "error": "archive_encrypted",
                            "message": "Архив защищён паролем — загрузите его без пароля",
                        }
                    },
                    "invalid_archive": {
                        "value": {"error": "invalid_archive", "message": "Архив повреждён"}
                    },
                    "empty_file": {"value": {"error": "empty_file", "message": "Файл пустой"}},
                    "invalid_tooth": {"value": _INVALID_TOOTH},
                    "taken_date_in_future": {"value": _FUTURE_DATE},
                },
            }
        },
    },
    401: {
        "description": (
            "Нет access-токена или он невалиден. Проверяется ДО приёма тела: файл "
            "без токена на сервер не загружается вовсе"
        ),
        "content": {
            "application/json": {
                "example": {"error": "invalid_token", "message": "Невалидный токен"}
            }
        },
    },
    413: {
        "description": "`Content-Length` больше лимита КТ — отказ до приёма тела",
        "content": {
            "application/json": {
                "example": {"error": "file_too_large", "message": "Файл больше 1024 МБ"}
            }
        },
    },
}

_LIST_ERRORS: dict[int | str, dict[str, Any]] = {
    **_NOT_FOUND,
    422: {
        "description": "Неверный номер зуба либо `tooth` вместе с `general=true`",
        "content": {
            "application/json": {
                "examples": {
                    "invalid_tooth": {"value": _INVALID_TOOTH},
                    "conflicting_filters": {
                        "value": {
                            "error": "conflicting_filters",
                            "message": "Нельзя одновременно просить файлы зуба и только общие",
                        }
                    },
                }
            }
        },
    },
}

_DELETE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_NOT_FOUND,
    403: {
        "description": "Файл загружен другой клиникой: менять и удалять его может только она",
        "content": {
            "application/json": {
                "example": {
                    "error": "file_not_owned",
                    "message": (
                        "Файл загружен другой клиникой — изменить или удалить его может только она"
                    ),
                }
            }
        },
    },
}

_UPDATE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_DELETE_ERRORS,
    422: {
        "description": "Неверный номер зуба, дата снимка из будущего или `teeth: null`",
        "content": {
            "application/json": {
                "examples": {
                    "invalid_tooth": {"value": _INVALID_TOOTH},
                    "taken_date_in_future": {"value": _FUTURE_DATE},
                }
            }
        },
    },
}


@router.post(
    "/clinic/patients/{patient_id}/files",
    response_model=PatientFileOut,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить файл в карту пациента или к зубам",
    response_description="Метаданные сохранённого файла",
    responses=_UPLOAD_ERRORS,
)
async def upload_patient_file(
    patient_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="Снимок, документ или КТ")],
    appointment_id: Annotated[
        uuid.UUID | None, Form(alias="appointmentId", description="Приём, к которому относится")
    ] = None,
    comment: Annotated[
        str | None, Form(max_length=500, description="Описание файла (заметка к нему)")
    ] = None,
    teeth: Annotated[
        list[int] | None,
        Form(
            description=(
                "Зубы FDI, к которым относится файл: поле повторяется на каждый зуб "
                "(`teeth=36`, `teeth=37`). Не передано — общий файл пациента"
            ),
        ),
    ] = None,
    taken_date: Annotated[
        date | None,
        Form(alias="takenDate", description="Дата самого снимка (ГГГГ-ММ-ДД), если известна"),
    ] = None,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: UploadPatientFileUseCase = Depends(get_upload_patient_file_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> PatientFileOut:
    """Снимок, документ или КТ в карту пациента (multipart).

    Файл кладётся либо в карту целиком (общий снимок, например КТ всей
    челюсти), либо к зубам — тогда он показывается у них в формуле. Один файл
    может относиться к нескольким зубам: прицельный снимок обычно захватывает
    соседние.

    Тип определяется **по содержимому**, а не по заголовку запроса: клиент
    называет тип сам, и `image/jpeg` с чем угодно внутри — это то, что потом
    скачает сотрудник. Поддерживаются JPEG, PNG, WebP, HEIC, PDF и КТ:
    одиночный DICOM-файл или ZIP-архив исследования. У КТ свой потолок размера
    (`UPLOAD_CT_MAX_BYTES`, по умолчанию 1 ГиБ), у остальных — 20 МБ. Архив КТ
    не распаковывается, но должен содержать DICOM и не содержать программ
    (программу просмотра с диска из архива нужно убрать).

    Имя файла на диске генерируем мы (uuid + расширение); присланное имя
    хранится отдельно и только для показа — иначе `../` в имени уводит запись за
    пределы хранилища.

    ⚠️ Пациент **глобальный**, поэтому файл увидят все клиники, которые его
    лечат, — как и сквозную историю зуба. Править и удалять его сможет только
    загрузившая клиника.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    view = await use_case.execute(
        context.clinic_id,
        context.account_id,
        patient_id,
        file,
        appointment_id=appointment_id,
        comment=comment,
        teeth=teeth,
        taken_date=taken_date,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="patient.file_upload",
        entity_type="patient",
        entity_id=patient_id,
        payload={
            "fileId": str(view.record.id),
            "kind": view.record.kind.value,
            "teeth": view.record.tooth_numbers,
        },
    )
    return PatientFileOut.from_view(view, context.clinic_id)


@router.get(
    "/clinic/patients/{patient_id}/files",
    response_model=PatientFilesResponse,
    summary="Файлы пациента",
    response_description="Метаданные файлов (свежие по дате снимка сверху)",
    responses=_LIST_ERRORS,
)
async def list_patient_files(
    patient_id: uuid.UUID,
    source: FileSource = Query(
        default=FileSource.OWN,
        description=(
            "Чьи файлы: `own` — этой клиники (по умолчанию), `others` — других клиник, `all` — все"
        ),
    ),
    tooth: int | None = Query(default=None, description="Только файлы этого зуба (FDI)"),
    general: bool = Query(
        default=False, description="Только общие файлы пациента (без привязки к зубам)"
    ),
    appointment_id: uuid.UUID | None = Query(
        default=None, alias="appointmentId", description="Только файлы этого приёма"
    ),
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: ListPatientFilesUseCase = Depends(get_list_patient_files_use_case),
) -> PatientFilesResponse:
    """Файлы карты пациента с отбором по источнику, зубу и приёму.

    По умолчанию — только файлы **этой клиники**. Файлы других клиник, которые
    лечат того же пациента, — по `source=others` (или `all`): в ответе у
    каждого есть `isOwn` и `clinicName`. `othersCount` — сколько под тем же
    отбором чужих файлов, чтобы показать «есть N файлов из других клиник», не
    загружая их.

    Сортировка — по дате снимка (`takenDate`, а если её нет — по дате
    загрузки), свежие сверху.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    views, others_count = await use_case.execute(
        context.clinic_id,
        patient_id,
        source=source,
        tooth=tooth,
        general=general,
        appointment_id=appointment_id,
    )
    return PatientFilesResponse(
        items=[PatientFileOut.from_view(v, context.clinic_id) for v in views],
        others_count=others_count,
    )


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
    """Отдать содержимое файла (в том числе загруженного другой клиникой).

    Заголовок ``Content-Disposition: attachment`` и запрет угадывания типа
    (``nosniff``) стоят намеренно: файл открывается на нашем домене, где у
    сотрудника живая сессия, и показывать его инлайн — лишний риск.

    Имя для скачивания — присланное, но с расширением по **настоящему** типу
    (`viewer.exe`, прошедший как DICOM, скачается как `viewer.dcm`): иначе
    чужая клиника получала бы запускаемый файл.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    record = await use_case.execute(context.clinic_id, patient_id, file_id)
    return FileResponse(
        resolve_stored_path(record.stored_path),
        media_type=record.mime,
        filename=download_name(record.original_name, record.stored_path),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.patch(
    "/clinic/patients/{patient_id}/files/{file_id}",
    response_model=PatientFileOut,
    summary="Изменить описание, зубы или дату снимка",
    response_description="Файл после правки",
    responses=_UPDATE_ERRORS,
)
async def update_patient_file(
    patient_id: uuid.UUID,
    file_id: uuid.UUID,
    body: UpdatePatientFileRequest,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: UpdatePatientFileUseCase = Depends(get_update_patient_file_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> PatientFileOut:
    """Поправить метаданные своего файла; содержимое не меняется.

    Меняются только присланные поля: `comment` (описание), `teeth` (`[]` —
    отвязать от зубов, файл станет общим) и `takenDate`. Править может **только
    клиника, загрузившая файл**: другие клиники пациента файл видят, но на
    правку получают 403 `file_not_owned`.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    fields = body.to_fields()
    view = await use_case.execute(context.clinic_id, patient_id, file_id, fields)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="patient.file_update",
        entity_type="patient",
        entity_id=patient_id,
        payload={"fileId": str(file_id), "fields": sorted(fields)},
    )
    return PatientFileOut.from_view(view, context.clinic_id)


@router.delete(
    "/clinic/patients/{patient_id}/files/{file_id}",
    response_model=FileDeletedResponse,
    summary="Удалить файл пациента",
    response_description="Файл помечен удалённым",
    responses=_DELETE_ERRORS,
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
    навсегда». Удалить может **только клиника, загрузившая файл** (иначе 403
    `file_not_owned`): иначе любая клиника пациента стирала бы чужие снимки.
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
