"""Доменная логика тарифов: модель «пакеты» + доплата за место («Сеть»).

Клиника выбирает **пакет** по числу мест: пакет покрывает клинику до ``up_to``
мест за фиксированную ``price``. Границы ``up_to`` и цены ``price`` строго
возрастают. Сверх последнего пакета — доплата ``over_price`` за каждое место
(гибридная ступень «Сеть», 11+). Админ — бесплатно, минимум 2 места.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ValidationAppError

_MIN_SEATS = 2


@dataclass(frozen=True, slots=True)
class PackageDef:
    name: str
    up_to: int
    price: int


def validate_packages(packages: list[PackageDef], over_price: int) -> None:
    """Проверить пакеты и доплату. Бросает ValidationAppError (422) при ошибке."""
    if not packages:
        raise ValidationAppError("Нужен хотя бы один пакет", code="tariff_no_packages")
    if over_price <= 0:
        raise ValidationAppError("Доплата за место должна быть > 0", code="invalid_over_price")

    ordered = sorted(packages, key=lambda p: p.up_to)
    if ordered[0].up_to < _MIN_SEATS:
        raise ValidationAppError(
            "Первый пакет должен покрывать минимум 2 места", code="tariff_min_seats"
        )
    prev_up_to: int | None = None
    prev_price: int | None = None
    for pkg in ordered:
        if pkg.price <= 0:
            raise ValidationAppError("Цена пакета должна быть > 0", code="invalid_price")
        if prev_up_to is not None and pkg.up_to <= prev_up_to:
            raise ValidationAppError(
                "Границы up_to должны строго возрастать", code="tariff_not_increasing"
            )
        if prev_price is not None and pkg.price <= prev_price:
            raise ValidationAppError(
                "Цены пакетов должны строго возрастать", code="tariff_not_increasing"
            )
        prev_up_to = pkg.up_to
        prev_price = pkg.price


def compute_clinic_price(packages: list[PackageDef], over_price: int, seats: int) -> int:
    """Цена клиники за ``seats`` мест: пакет по числу мест или пакет + «Сеть»."""
    ordered = sorted(packages, key=lambda p: p.up_to)
    for pkg in ordered:
        if seats <= pkg.up_to:
            return pkg.price
    last = ordered[-1]
    return last.price + over_price * (seats - last.up_to)


def package_for(packages: list[PackageDef], seats: int) -> PackageDef | None:
    """Пакет, который покрывает ``seats`` мест; ``None`` — мест больше последнего пакета.

    Сверх последнего пакета действует «Сеть»: его цена плюс доплата за место.
    """
    for pkg in sorted(packages, key=lambda p: p.up_to):
        if seats <= pkg.up_to:
            return pkg
    return None


def package_summary(packages: list[PackageDef], over_price: int) -> str:
    """Краткое описание версии клиники: «до 3: 3 000 · … · 11+: 800/место»."""
    ordered = sorted(packages, key=lambda p: p.up_to)
    parts = [f"до {p.up_to}: {p.price}" for p in ordered]
    if ordered:
        parts.append(f"{ordered[-1].up_to + 1}+: {over_price}/место")
    return " · ".join(parts)
