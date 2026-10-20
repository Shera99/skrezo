"""Репозиторий файлов пациента (метаданные; сам файл — на диске)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import ColumnElement, Date, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.patients.domain.models import (
    FileSource,
    PatientFilesSummary,
    PatientFileView,
    ToothFilesCount,
)
from app.shared.db.enums import FileKind
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient_file import PatientFile
from app.shared.db.updates import apply_updates

#: Что PATCH вправе менять у файла. Сам файл, вид и принадлежность — нет:
#: замена содержимого — это новая загрузка.
_UPDATABLE = frozenset({"comment", "tooth_numbers", "taken_date"})
_NOT_NULL = frozenset({"tooth_numbers"})


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
        tooth_numbers: list[int],
        taken_date: date | None,
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
            tooth_numbers=tooth_numbers,
            taken_date=taken_date,
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
        self,
        patient_id: uuid.UUID,
        *,
        clinic_id: uuid.UUID,
        source: FileSource,
        tooth: int | None = None,
        general: bool = False,
        appointment_id: uuid.UUID | None = None,
    ) -> list[PatientFileView]:
        """Файлы пациента (свежие по дате снимка сверху), кроме удалённых.

        Пациент глобальный, и карта у него одна на всю платформу — как и
        сквозная история зуба: ``source`` лишь выбирает, чьи файлы показать.

        Без даты снимка файл встаёт по дате загрузки — в поясе запрашивающей
        клиники: ``taken_date`` — её «календарная» дата, а по UTC загруженное
        в Бишкеке с полуночи до шести утра оказалось бы «вчерашним».
        """
        clinic_tz = select(Clinic.timezone).where(Clinic.id == clinic_id).scalar_subquery()
        uploaded_on = cast(func.timezone(clinic_tz, PatientFile.created_at), Date)
        query = (
            select(PatientFile, Clinic.name)
            .join(Clinic, Clinic.id == PatientFile.clinic_id)
            .where(
                *self._filters(
                    patient_id,
                    clinic_id=clinic_id,
                    source=source,
                    tooth=tooth,
                    general=general,
                    appointment_id=appointment_id,
                )
            )
            .order_by(
                desc(func.coalesce(PatientFile.taken_date, uploaded_on)),
                desc(PatientFile.created_at),
            )
        )
        rows = (await self._session.execute(query)).all()
        return [PatientFileView(record=row[0], clinic_name=row[1]) for row in rows]

    async def count(
        self,
        patient_id: uuid.UUID,
        *,
        clinic_id: uuid.UUID,
        source: FileSource,
        tooth: int | None = None,
        general: bool = False,
        appointment_id: uuid.UUID | None = None,
    ) -> int:
        """Сколько файлов под тем же отбором — для «ещё N из других клиник»."""
        return (
            await self._session.execute(
                select(func.count())
                .select_from(PatientFile)
                .where(
                    *self._filters(
                        patient_id,
                        clinic_id=clinic_id,
                        source=source,
                        tooth=tooth,
                        general=general,
                        appointment_id=appointment_id,
                    )
                )
            )
        ).scalar_one()

    @staticmethod
    def _filters(
        patient_id: uuid.UUID,
        *,
        clinic_id: uuid.UUID,
        source: FileSource,
        tooth: int | None,
        general: bool,
        appointment_id: uuid.UUID | None,
    ) -> list[ColumnElement[bool]]:
        """Условия отбора файлов — общие для списка и счётчика."""
        conditions: list[ColumnElement[bool]] = [
            PatientFile.patient_id == patient_id,
            PatientFile.deleted_at.is_(None),
        ]
        # Каждая ветка — явно, а незнакомое значение — ошибка: отбор по клинике
        # должен закрываться, а не молча отдавать все файлы пациента.
        match FileSource(source):
            case FileSource.OWN:
                conditions.append(PatientFile.clinic_id == clinic_id)
            case FileSource.OTHERS:
                conditions.append(PatientFile.clinic_id != clinic_id)
            case FileSource.ALL:
                pass
        if tooth is not None:
            conditions.append(PatientFile.tooth_numbers.contains([tooth]))
        if general:
            conditions.append(func.cardinality(PatientFile.tooth_numbers) == 0)
        if appointment_id is not None:
            conditions.append(PatientFile.appointment_id == appointment_id)
        return conditions

    async def summary(self, patient_id: uuid.UUID, clinic_id: uuid.UUID) -> PatientFilesSummary:
        """Счётчики для карточки: свои и чужие файлы, всего и по каждому зубу."""
        own = PatientFile.clinic_id == clinic_id
        alive = (PatientFile.patient_id == patient_id, PatientFile.deleted_at.is_(None))
        totals = (
            await self._session.execute(
                select(
                    func.count().filter(own),
                    func.count().filter(~own),
                ).where(*alive)
            )
        ).one()
        # unnest в подзапросе: в GROUP BY функцию, возвращающую набор строк,
        # PostgreSQL не пускает.
        per_tooth = (
            select(
                func.unnest(PatientFile.tooth_numbers).label("tooth"),
                own.label("is_own"),
            )
            .where(*alive)
            .subquery()
        )
        rows = (
            await self._session.execute(
                select(
                    per_tooth.c.tooth,
                    func.count().filter(per_tooth.c.is_own),
                    func.count().filter(~per_tooth.c.is_own),
                )
                .group_by(per_tooth.c.tooth)
                .order_by(per_tooth.c.tooth)
            )
        ).all()
        return PatientFilesSummary(
            own=totals[0],
            others=totals[1],
            teeth=[ToothFilesCount(tooth=r[0], own=r[1], others=r[2]) for r in rows],
        )

    async def clinic_name(self, clinic_id: uuid.UUID) -> str:
        """Название клиники — для ответа на загрузку и правку своего файла."""
        return (
            await self._session.execute(select(Clinic.name).where(Clinic.id == clinic_id))
        ).scalar_one()

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

    async def update(self, record: PatientFile, fields: dict[str, Any]) -> None:
        """Правка описания, зубов и даты снимка — строго по списку колонок."""
        apply_updates(
            record,
            fields,
            updatable=_UPDATABLE,
            not_null=_NOT_NULL,
            entity_name="файла пациента",
        )
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
