"""Схемы модуля пациентов (глобальный пациент, одонтограмма, история зуба)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import ConfigDict, Field

from app.modules.patients.domain.models import (
    PatientCard,
    PatientListRow,
    ToothHistoryEntry,
)
from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import AppointmentItemStatus, FileKind, ToothStatus
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_file import PatientFile
from app.shared.db.models.patient_tooth import PatientTooth


class PatientListItem(CamelModel):
    """Пациент в списке клиники — с последним визитом и долгом в этой клинике."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3f6c1b2a-8d4e-4f5a-9b7c-1e2d3c4b5a60",
                "phone": "+996700123456",
                "firstName": "Айгүл",
                "lastName": "Асанова",
                "birthDate": "1990-04-12",
                "gender": "female",
                "lastVisitAt": "2026-03-14T10:00:00+06:00",
                "debtTiyin": 150000,
            }
        }
    )

    id: uuid.UUID
    phone: str
    first_name: str
    last_name: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    last_visit_at: datetime | None = Field(
        default=None,
        description=(
            "Последний завершённый приём в этой клинике; `null` — завершённых ещё не было. "
            "По нему «Напоминания» находят, кого пора звать на повторный осмотр"
        ),
        examples=["2026-03-14T10:00:00+06:00"],
    )
    debt_tiyin: int = Field(
        default=0,
        description=(
            "Долг пациента в этой клинике, тыйыны: сумма остатков завершённых приёмов "
            "(цена − платежи + возвраты), как в `GET /clinic/cash/debts`. Виден всем, у кого "
            "есть доступ к пациентам"
        ),
        examples=[150000],
    )

    @classmethod
    def from_row(cls, r: PatientListRow) -> PatientListItem:
        return cls(
            id=r.id,
            phone=r.phone,
            first_name=r.first_name,
            last_name=r.last_name,
            birth_date=r.birth_date,
            gender=r.gender,
            last_visit_at=r.last_visit_at,
            debt_tiyin=r.debt_tiyin,
        )


class PatientsResponse(CamelModel):
    list: list[PatientListItem]
    current_page: int
    page_size: int = 25
    total_count: int


class PatientIdentity(CamelModel):
    id: uuid.UUID
    phone: str
    first_name: str
    last_name: str | None = None
    middle_name: str | None = None
    birth_date: date | None = None
    gender: str | None = None

    @classmethod
    def from_patient(cls, p: Patient) -> PatientIdentity:
        return cls(
            id=p.id,
            phone=p.phone,
            first_name=p.first_name,
            last_name=p.last_name,
            middle_name=p.middle_name,
            birth_date=p.birth_date,
            gender=p.gender,
        )


class PatientLookupResponse(CamelModel):
    patient: PatientIdentity | None = Field(default=None, description="Найденный пациент или null")


class OdontogramToothOut(CamelModel):
    tooth: int = Field(description="Номер зуба FDI")
    status: ToothStatus
    note: str | None = None
    updated_at: datetime

    @classmethod
    def from_orm_tooth(cls, t: PatientTooth) -> OdontogramToothOut:
        return cls(tooth=t.tooth_number, status=t.status, note=t.note, updated_at=t.updated_at)


class PatientCardResponse(CamelModel):
    id: uuid.UUID
    phone: str
    first_name: str
    last_name: str | None = None
    middle_name: str | None = None
    gender: str | None = None
    birth_date: date | None = None
    email: str | None = None
    avatar: str | None = None
    note: str | None = Field(
        default=None,
        description="Заметка о пациенте (аллергии, особенности приёма)",
        examples=["Аллергия на лидокаин"],
    )
    created_at: datetime
    appointments_count: int
    odontogram: list[OdontogramToothOut]

    @classmethod
    def from_card(cls, card: PatientCard) -> PatientCardResponse:
        p = card.patient
        return cls(
            id=p.id,
            phone=p.phone,
            first_name=p.first_name,
            last_name=p.last_name,
            middle_name=p.middle_name,
            gender=p.gender,
            birth_date=p.birth_date,
            email=p.email,
            avatar=p.avatar,
            note=p.note,
            created_at=p.created_at,
            appointments_count=card.appointments_count,
            odontogram=[OdontogramToothOut.from_orm_tooth(t) for t in card.odontogram],
        )


class CreatePatientRequest(CamelModel):
    phone: str = Field(description="Телефон (уникален на всю платформу)")
    first_name: str = Field(min_length=1, description="Имя")
    last_name: str | None = None
    middle_name: str | None = None
    gender: str | None = Field(default=None, description="male|female|other")
    birth_date: date | None = None
    email: str | None = None
    note: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Заметка о пациенте (аллергии, особенности приёма). Пациент глобальный — "
            "заметку видит любая клиника, где он лечится"
        ),
        examples=["Аллергия на лидокаин"],
    )


class PatientCreatedResponse(CamelModel):
    id: uuid.UUID


class UpdatePatientRequest(NotNullFieldsModel):
    """Правка пациента: передаются только изменяемые поля.

    ``null`` осмыслен лишь для NULLABLE-колонок (снять отчество, пол, email).
    Для NOT NULL-поля ``firstName`` ``null`` — ошибка валидации (422). Телефон
    здесь не правится: это ключ дедупликации пациента на всю платформу.
    """

    NOT_NULL_FIELDS = ("firstName",)

    first_name: str | None = Field(default=None, min_length=1)
    last_name: str | None = None
    middle_name: str | None = None
    gender: str | None = None
    birth_date: date | None = None
    email: str | None = None
    note: str | None = Field(
        default=None,
        max_length=2000,
        description="Заметка о пациенте; `null` — снять заметку",
        examples=["Аллергия на лидокаин"],
    )


class ToothStateInput(CamelModel):
    tooth: int = Field(description="Номер зуба FDI (11–48, 51–85)")
    status: ToothStatus
    note: str | None = None


class UpsertTeethRequest(CamelModel):
    teeth: list[ToothStateInput] = Field(min_length=1, description="Карта зубов (upsert)")


class OdontogramResponse(CamelModel):
    odontogram: list[OdontogramToothOut]


class ToothHistoryItem(CamelModel):
    appointment_id: uuid.UUID
    date: datetime
    clinic_name: str
    doctor_name: str | None = None
    service_name: str | None = None
    status: AppointmentItemStatus
    price_tiyin: int = Field(description="Цена в тыйынах")
    comment: str | None = None

    @classmethod
    def from_entry(cls, e: ToothHistoryEntry) -> ToothHistoryItem:
        return cls(
            appointment_id=e.appointment_id,
            date=e.date,
            clinic_name=e.clinic_name,
            doctor_name=e.doctor_name,
            service_name=e.service_name,
            status=e.status,
            price_tiyin=e.price_tiyin,
            comment=e.comment,
        )


class ToothHistoryResponse(CamelModel):
    tooth: int
    records: list[ToothHistoryItem]


class PatientFileOut(CamelModel):
    """Файл в карте пациента (метаданные; содержимое — по отдельной ручке)."""

    id: uuid.UUID
    kind: FileKind = Field(description="`images` — снимок, `files` — документ")
    original_name: str = Field(description="Имя, под которым файл загрузили")
    mime: str = Field(examples=["image/jpeg"])
    size_bytes: int
    comment: str | None = None
    appointment_id: uuid.UUID | None = Field(
        default=None, description="Приём, к которому относится файл (если указан)"
    )
    clinic_id: uuid.UUID = Field(description="Клиника, которая загрузила файл")
    uploaded_by_membership_id: uuid.UUID | None = None
    created_at: datetime

    @classmethod
    def from_record(cls, record: PatientFile) -> PatientFileOut:
        return cls(
            id=record.id,
            kind=record.kind,
            original_name=record.original_name,
            mime=record.mime,
            size_bytes=record.size_bytes,
            comment=record.comment,
            appointment_id=record.appointment_id,
            clinic_id=record.clinic_id,
            uploaded_by_membership_id=record.uploaded_by_membership_id,
            created_at=record.created_at,
        )


class PatientFilesResponse(CamelModel):
    items: list[PatientFileOut]


class FileDeletedResponse(CamelModel):
    id: uuid.UUID
