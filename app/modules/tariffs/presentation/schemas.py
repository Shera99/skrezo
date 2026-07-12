"""Схемы тарифов (контракты фронта): модель «пакеты» + доплата «Сеть»."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.shared.api.schemas import CamelModel


class PackageSchema(CamelModel):
    name: str = Field(description="Название пакета")
    up_to: int = Field(ge=2, description="Верхняя граница мест (включительно)")
    price: int = Field(gt=0, description="Цена за клинику до up_to мест, сом/мес")


class SoloTariffOut(CamelModel):
    version: int
    updated_at: datetime
    price: int


class ClinicTariffOut(CamelModel):
    version: int
    updated_at: datetime
    packages: list[PackageSchema]
    over_price: int = Field(description="Цена за место сверх последнего пакета («Сеть»)")


class TariffHistoryItem(CamelModel):
    id: uuid.UUID
    kind: str = Field(description="solo | clinic")
    version: int
    date: datetime
    current: bool = Field(description="Текущая ли это версия своей линейки")
    summary: str = Field(description="Краткое описание версии для истории")


class TariffsBundle(CamelModel):
    solo: SoloTariffOut | None = None
    clinic: ClinicTariffOut | None = None
    history: list[TariffHistoryItem] = Field(default_factory=list)


class UpdateSoloRequest(CamelModel):
    price: int = Field(gt=0, description="Фиксированная цена соло, сом/мес")


class UpdateClinicRequest(CamelModel):
    packages: list[PackageSchema] = Field(min_length=1, description="Пакеты по возрастанию мест")
    over_price: int = Field(gt=0, description="Доплата за место сверх последнего пакета")


class SoloTariffCreatedResponse(CamelModel):
    tariff_version_id: uuid.UUID
    version: int


class ClinicTariffCreatedResponse(CamelModel):
    tariff_version_id: uuid.UUID
    version: int
    affected_clients: int = Field(description="Сколько клиник затронет смена (со след. продления)")
