"""Доменные структуры заявок клиник."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.shared.db.enums import ClinicApplicationStatus, DoctorsCountRange


@dataclass(frozen=True, slots=True)
class ApplicationRow:
    """Заявка для списка/карточки в админке (с названием города)."""

    id: uuid.UUID
    company_name: str
    city_id: uuid.UUID
    city_name: str | None
    doctors_count: DoctorsCountRange
    phone: str
    status: ClinicApplicationStatus
    reviewed_by_account_id: uuid.UUID | None
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ApplicationsPage:
    """Страница списка заявок (offset-пагинация, как в остальной админке)."""

    items: list[ApplicationRow]
    total_count: int
    current_page: int
    page_size: int
