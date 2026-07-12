"""Юнит-тесты валидации формы создания клиники (без БД)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.clinics.presentation.schemas import CreateClinicRequest
from app.shared.db.enums import ClinicType


def test_solo_minimal_ok() -> None:
    req = CreateClinicRequest(adminFullName="Иван", adminPhone="0700111222", type=ClinicType.SOLO)
    assert req.type is ClinicType.SOLO


def test_clinic_requires_extra_fields() -> None:
    # Для клиники обязательны name/cityId/businessPhone/seatsLimit.
    with pytest.raises(ValidationError):
        CreateClinicRequest(
            adminFullName="Иван", adminPhone="0700111222", type=ClinicType.CLINIC
        )


def test_clinic_full_ok() -> None:
    req = CreateClinicRequest(
        type=ClinicType.CLINIC,
        name="Клиника",
        cityId="0f9b7c1e-2a3d-4b5c-8e9f-1a2b3c4d5e6f",
        businessPhone="0312900900",
        adminFullName="Админ",
        adminPhone="0700333444",
        seatsLimit=5,
    )
    assert req.seats_limit == 5
