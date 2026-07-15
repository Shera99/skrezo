"""Схемы модуля пациентов (карточка, одонтограмма, визиты, история зуба)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field

from app.modules.patients.domain.models import (
    OdontogramTooth,
    PatientDetail,
    PatientListRow,
    VisitDetail,
    VisitRow,
)
from app.shared.api.schemas import CamelModel
from app.shared.db.models.tooth_record import ToothRecord


class PatientListItem(CamelModel):
    id: uuid.UUID
    full_name: str
    phone: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, row: PatientListRow) -> PatientListItem:
        return cls(
            id=row.id,
            full_name=row.full_name,
            phone=row.phone,
            birth_date=row.birth_date,
            gender=row.gender,
            created_at=row.created_at,
        )


class PatientsResponse(CamelModel):
    list: list[PatientListItem]
    current_page: int
    page_size: int = 25
    total_count: int


class OdontogramToothOut(CamelModel):
    tooth: int = Field(description="Номер зуба FDI")
    status: str | None = Field(default=None, description="Текущее состояние (последняя запись)")
    history_count: int = Field(description="Сколько записей в истории зуба")
    last_at: datetime

    @classmethod
    def from_domain(cls, t: OdontogramTooth) -> OdontogramToothOut:
        return cls(tooth=t.tooth, status=t.status, history_count=t.history_count, last_at=t.last_at)


class PatientDetailResponse(CamelModel):
    id: uuid.UUID
    full_name: str
    phone: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    note: str | None = None
    created_at: datetime
    visits_count: int
    odontogram: list[OdontogramToothOut]

    @classmethod
    def from_detail(cls, detail: PatientDetail) -> PatientDetailResponse:
        p = detail.patient
        return cls(
            id=p.id,
            full_name=p.full_name,
            phone=p.phone,
            birth_date=p.birth_date,
            gender=p.gender,
            note=p.note,
            created_at=p.created_at,
            visits_count=detail.visits_count,
            odontogram=[OdontogramToothOut.from_domain(t) for t in detail.odontogram],
        )


class CreatePatientRequest(CamelModel):
    full_name: str = Field(min_length=2, description="ФИО пациента")
    phone: str | None = None
    birth_date: date | None = None
    gender: str | None = Field(default=None, description="male|female|other (свободно)")
    note: str | None = None


class UpdatePatientRequest(CamelModel):
    full_name: str | None = Field(default=None, min_length=2)
    phone: str | None = None
    birth_date: date | None = None
    gender: str | None = None
    note: str | None = None


class PatientCreatedResponse(CamelModel):
    id: uuid.UUID


class ToothRecordOut(CamelModel):
    id: uuid.UUID
    tooth: int
    visit_id: uuid.UUID
    diagnosis: str | None = None
    treatment: str | None = None
    status: str | None = None
    note: str | None = None
    created_at: datetime

    @classmethod
    def from_orm_record(cls, r: ToothRecord) -> ToothRecordOut:
        return cls(
            id=r.id,
            tooth=r.tooth,
            visit_id=r.visit_id,
            diagnosis=r.diagnosis,
            treatment=r.treatment,
            status=r.status,
            note=r.note,
            created_at=r.created_at,
        )


class ToothHistoryResponse(CamelModel):
    tooth: int
    records: list[ToothRecordOut]


class VisitListItem(CamelModel):
    id: uuid.UUID
    visited_at: datetime
    doctor_name: str | None = None
    complaint: str | None = None
    records_count: int

    @classmethod
    def from_row(cls, row: VisitRow) -> VisitListItem:
        return cls(
            id=row.id,
            visited_at=row.visited_at,
            doctor_name=row.doctor_name,
            complaint=row.complaint,
            records_count=row.records_count,
        )


class VisitRecordInput(CamelModel):
    tooth: int = Field(description="Номер зуба FDI (11–48, 51–85)")
    diagnosis: str | None = None
    treatment: str | None = None
    status: str | None = None
    note: str | None = None


class CreateVisitRequest(CamelModel):
    visited_at: datetime | None = Field(default=None, description="Дата/время приёма (по умолч.)")
    doctor_membership_id: uuid.UUID | None = Field(default=None, description="Врач (членство)")
    complaint: str | None = None
    note: str | None = None
    records: list[VisitRecordInput] = Field(
        default_factory=list, description="Манипуляции по зубам в рамках визита"
    )


class VisitCreatedResponse(CamelModel):
    id: uuid.UUID


class VisitDetailResponse(CamelModel):
    id: uuid.UUID
    visited_at: datetime
    doctor_name: str | None = None
    complaint: str | None = None
    note: str | None = None
    records: list[ToothRecordOut]

    @classmethod
    def from_detail(cls, detail: VisitDetail) -> VisitDetailResponse:
        v = detail.visit
        return cls(
            id=v.id,
            visited_at=v.visited_at,
            doctor_name=detail.doctor_name,
            complaint=v.complaint,
            note=v.note,
            records=[ToothRecordOut.from_orm_record(r) for r in detail.records],
        )
