"""Доступ клиники к карте пациента: «не наш» и «не существует» — один ответ.

Пациент глобальный, а клиника видит его только при наличии приёма. Если на
чужого пациента отвечать 403, а на несуществующего — 404, то перебором
идентификаторов подтверждается существование людей, которых клиника не лечит:
«403» значит «такой пациент есть, но не ваш». Поэтому оба случая сведены в один
ответ 404 — и не двумя одинаковыми ветками, а одной проверкой, чтобы их нельзя
было развести случайной правкой.

Отдельный путь привязки нового пациента остаётся: ``GET /clinic/patients/lookup``
по ТОЧНОМУ телефону — это осознанный продуктовый флоу дедупликации, а не перебор.
"""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.patients.infrastructure.repositories.patient_repository import PatientRepository
from app.shared.db.models.patient import Patient


async def ensure_clinic_patient(
    clinic_id: uuid.UUID, patient_id: uuid.UUID, patients: PatientRepository
) -> Patient:
    """Вернуть пациента, которого лечит эта клиника; иначе 404."""
    patient = await patients.get(patient_id)
    if patient is None or not await patients.clinic_treats(clinic_id, patient_id):
        raise NotFoundError("Пациент не найден", code="patient_not_found")
    return patient
