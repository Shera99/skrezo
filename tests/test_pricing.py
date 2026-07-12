"""Юнит-тесты ценообразования и валидации пакетов клиники (без БД)."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationAppError
from app.modules.tariffs.domain.pricing import (
    PackageDef,
    compute_clinic_price,
    package_summary,
    validate_packages,
)

_OVER = 800


def _packages() -> list[PackageDef]:
    # до 3 = 3000, до 6 = 5000, до 10 = 8000; сверх 10 — 800/место.
    return [PackageDef("Старт", 3, 3000), PackageDef("Про", 6, 5000), PackageDef("Макс", 10, 8000)]


def test_compute_within_packages() -> None:
    pkgs = _packages()
    assert compute_clinic_price(pkgs, _OVER, 2) == 3000
    assert compute_clinic_price(pkgs, _OVER, 3) == 3000
    assert compute_clinic_price(pkgs, _OVER, 4) == 5000
    assert compute_clinic_price(pkgs, _OVER, 6) == 5000
    assert compute_clinic_price(pkgs, _OVER, 10) == 8000


def test_compute_network_over_last_package() -> None:
    pkgs = _packages()
    # 11+ = цена последнего пакета + over_price × (мест − up_to последнего).
    assert compute_clinic_price(pkgs, _OVER, 11) == 8000 + 800
    assert compute_clinic_price(pkgs, _OVER, 15) == 8000 + 800 * 5


def test_price_grows_monotonically() -> None:
    pkgs = _packages()
    assert compute_clinic_price(pkgs, _OVER, 10) < compute_clinic_price(pkgs, _OVER, 11)


def test_validate_ok() -> None:
    validate_packages(_packages(), _OVER)  # не бросает


def test_validate_min_seats() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_packages([PackageDef("Мини", 1, 1000)], _OVER)
    assert exc.value.code == "tariff_min_seats"


def test_validate_up_to_must_increase() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_packages([PackageDef("A", 3, 3000), PackageDef("B", 3, 5000)], _OVER)
    assert exc.value.code == "tariff_not_increasing"


def test_validate_price_must_increase() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_packages([PackageDef("A", 3, 5000), PackageDef("B", 6, 5000)], _OVER)
    assert exc.value.code == "tariff_not_increasing"


def test_validate_over_price_positive() -> None:
    with pytest.raises(ValidationAppError) as exc:
        validate_packages(_packages(), 0)
    assert exc.value.code == "invalid_over_price"


def test_summary_format() -> None:
    summary = package_summary(_packages(), _OVER)
    assert "до 3: 3000" in summary
    assert "11+: 800/место" in summary
