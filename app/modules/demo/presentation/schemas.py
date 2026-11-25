"""Схемы демо-клиники."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import ConfigDict, Field

from app.modules.demo.domain.models import DemoState, ResetResult
from app.shared.api.schemas import CamelModel


class SetDemoRequest(CamelModel):
    """Включить или выключить признак демо."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"demo": True, "confirmName": "Демо Skrezo"}}
    )

    demo: bool = Field(description="Включить (`true`) или снять (`false`) признак", examples=[True])
    confirm_name: str | None = Field(
        default=None,
        description=(
            "Название клиники — подтверждение включения (регистр не важен). При снятии "
            "признака не нужно"
        ),
        examples=["Демо Skrezo"],
    )


class DemoStateResponse(CamelModel):
    """Признак демо и граница, раньше которой сброс данные не трогает."""

    clinic_id: uuid.UUID
    demo: bool = Field(description="Клиника — демонстрационная", examples=[True])
    since: datetime | None = Field(
        default=None,
        description=(
            "С какого момента данные клиники демонстрационные: появившееся позже удаляет "
            "сброс, появившееся раньше он не трогает; `null` — признак не включали"
        ),
        examples=["2026-11-25T10:00:00Z"],
    )
    reset_at: datetime | None = Field(
        default=None,
        description="Когда день собран в последний раз; `null` — сброса ещё не было",
        examples=["2026-11-26T00:00:03Z"],
    )

    @classmethod
    def from_state(cls, state: DemoState) -> DemoStateResponse:
        return cls(
            clinic_id=state.clinic_id,
            demo=state.demo,
            since=state.since,
            reset_at=state.reset_at,
        )


class DemoDeleted(CamelModel):
    """Сколько удалил сброс."""

    patients: int = Field(description="Карточек пациентов", examples=[8])
    appointments: int = Field(description="Записей", examples=[9])
    payments: int = Field(description="Платежей и возвратов", examples=[3])
    conversations: int = Field(description="Переписок WhatsApp", examples=[1])


class DemoCreated(CamelModel):
    """Сколько собрал сброс."""

    patients: int = Field(description="Выдуманных пациентов", examples=[7])
    appointments: int = Field(description="Приёмов демо-дня", examples=[7])


class DemoResetResponse(CamelModel):
    """Итог сброса демо-клиники."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "resetAt": "2026-11-25T08:40:00Z",
                "deleted": {
                    "patients": 8,
                    "appointments": 9,
                    "payments": 3,
                    "conversations": 1,
                },
                "created": {"patients": 7, "appointments": 7},
            }
        }
    )

    reset_at: datetime = Field(description="Момент, от которого собран день")
    deleted: DemoDeleted
    created: DemoCreated

    @classmethod
    def from_result(cls, result: ResetResult) -> DemoResetResponse:
        return cls(
            reset_at=result.reset_at,
            deleted=DemoDeleted(
                patients=result.purged.patients,
                appointments=result.purged.appointments,
                payments=result.purged.payments,
                conversations=result.purged.conversations,
            ),
            created=DemoCreated(
                patients=len(result.patient_ids), appointments=len(result.appointment_ids)
            ),
        )
