"""API-тесты жизненного цикла приёма: статусы, переходы, причина отмены.

Цикл: ``draft → scheduled → confirmed → arrived → in_progress → completed``
плюс ``cancelled`` (с причиной) и ``no_show``.

Ключевые риски:

* завершённый приём не должен «расзавершаться» — за ним стоят платежи кассы;
* черновик не должен прыгать сразу в рабочие статусы мимо назначения даты;
* причина отмены не должна оставаться висеть на приёме, у которого отмену сняли;
* свобода внутри активных статусов должна сохраниться (регистратор ошибся —
  поправит), как и прежние сценарии: запись сразу в completed и снятие отмены.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from tests.conftest import API

BISHKEK = ZoneInfo("Asia/Bishkek")
_phone_seq = itertools.count(1)
_slot_seq = itertools.count()


def _phone() -> str:
    return f"070092{next(_phone_seq):04d}"


def _workday() -> date:
    today = datetime.now(BISHKEK).date()
    return today if today.weekday() != 6 else today - timedelta(days=1)


def _slot() -> str:
    """Уникальный слот в рабочем окне — приёмы одного врача не пересекаются."""
    index = next(_slot_seq)
    hour = (9, 10, 11, 14, 15, 16, 17)[index % 7]
    minute = 30 * ((index // 7) % 2)
    return datetime.combine(_workday(), time(hour, minute), tzinfo=BISHKEK).isoformat()


@dataclass(slots=True)
class ClinicCtx:
    headers: dict[str, str]
    patient_id: str


async def _login(client: AsyncClient, phone: str, password: str) -> dict[str, str]:
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Статусы",
            "adminPhone": phone,
            "name": "Клиника Статусы",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await _login(client, phone, created.json()["tempPassword"])
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Статусов"},
    )
    assert patient.status_code == 201, patient.text
    return ClinicCtx(headers=headers, patient_id=str(patient.json()["id"]))


async def _book(client: AsyncClient, ctx: ClinicCtx, **extra: Any) -> str:
    body: dict[str, Any] = {"patientId": ctx.patient_id, "scheduledAt": _slot(), **extra}
    resp = await client.post(f"{API}/clinic/appointments", headers=ctx.headers, json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _patch(
    client: AsyncClient, ctx: ClinicCtx, appointment_id: str, **body: Any
) -> Any:
    return await client.patch(
        f"{API}/clinic/appointments/{appointment_id}", headers=ctx.headers, json=body
    )


# ── 1. Полная цепочка ───────────────────────────────────────────────────────


async def test_full_lifecycle(client: AsyncClient, ctx: ClinicCtx) -> None:
    """scheduled → confirmed → arrived → in_progress → completed проходит целиком."""
    appointment_id = await _book(client, ctx)
    for status in ("confirmed", "arrived", "in_progress", "completed"):
        resp = await _patch(client, ctx, appointment_id, status=status)
        assert resp.status_code == 200, f"{status}: {resp.text}"
        assert resp.json()["status"] == status


async def test_active_statuses_allow_going_back(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Внутри активных статусов можно двигаться назад — ошибку регистратора чинят."""
    appointment_id = await _book(client, ctx)
    assert (await _patch(client, ctx, appointment_id, status="arrived")).status_code == 200
    back = await _patch(client, ctx, appointment_id, status="confirmed")
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "confirmed"


# ── 2. Защита завершённого приёма ───────────────────────────────────────────


@pytest.mark.parametrize(
    "target", ["scheduled", "confirmed", "arrived", "in_progress", "cancelled"]
)
async def test_completed_is_terminal(client: AsyncClient, ctx: ClinicCtx, target: str) -> None:
    """Завершённый приём не меняет статус: за ним стоят платежи кассы."""
    appointment_id = await _book(client, ctx)
    assert (await _patch(client, ctx, appointment_id, status="completed")).status_code == 200

    resp = await _patch(client, ctx, appointment_id, status=target)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "invalid_status_transition"


async def test_completed_stays_completed_after_rejected_transition(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Отклонённый переход ничего не портит — статус на месте."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="completed")
    await _patch(client, ctx, appointment_id, status="cancelled")

    card = await client.get(f"{API}/clinic/appointments/{appointment_id}", headers=ctx.headers)
    assert card.json()["status"] == "completed"


# ── 3. Черновик ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("target", ["confirmed", "arrived", "in_progress", "completed"])
async def test_draft_cannot_jump_into_work(
    client: AsyncClient, ctx: ClinicCtx, target: str
) -> None:
    """Из черновика — только в scheduled (с датой) или в отмену."""
    created = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": ctx.patient_id, "status": "draft"},
    )
    assert created.status_code == 201, created.text

    resp = await _patch(client, ctx, created.json()["id"], status=target, scheduledAt=_slot())
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "invalid_status_transition"


async def test_draft_can_be_cancelled(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Передумавшего пациента можно закрыть отменой прямо из черновика."""
    created = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": ctx.patient_id, "status": "draft"},
    )
    resp = await _patch(
        client, ctx, created.json()["id"], status="cancelled", cancelReason="Передумал"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["cancelReason"] == "Передумал"


# ── 4. Причина отмены ───────────────────────────────────────────────────────


async def test_cancel_reason_stored_and_cleared(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Причина живёт только у отменённого приёма: при снятии отмены очищается."""
    appointment_id = await _book(client, ctx)
    cancelled = await _patch(
        client, ctx, appointment_id, status="cancelled", cancelReason="Заболел врач"
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["cancelReason"] == "Заболел врач"

    restored = await _patch(client, ctx, appointment_id, status="scheduled")
    assert restored.status_code == 200, restored.text
    assert restored.json()["cancelReason"] is None


async def test_cancel_reason_without_cancel_is_422(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Причина отмены без отмены — ошибка, иначе она повиснет на рабочем приёме."""
    appointment_id = await _book(client, ctx)
    resp = await _patch(client, ctx, appointment_id, cancelReason="Просто так")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "cancel_reason_without_cancel"


async def test_cancel_without_reason_is_allowed(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Причина необязательна — отменить можно и молча."""
    appointment_id = await _book(client, ctx)
    resp = await _patch(client, ctx, appointment_id, status="cancelled")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cancelReason"] is None


# ── 5. Неявка и снятие отмены ───────────────────────────────────────────────


async def test_no_show_and_back(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Неявку можно поставить и снять (ошиблись — вернули в работу)."""
    appointment_id = await _book(client, ctx)
    marked = await _patch(client, ctx, appointment_id, status="no_show")
    assert marked.status_code == 200, marked.text
    back = await _patch(client, ctx, appointment_id, status="arrived")
    assert back.status_code == 200, back.text


async def test_arrived_counted_in_reports(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Новый статус виден в отчёте по приёмам отдельным счётчиком."""
    appointment_id = await _book(client, ctx)
    assert (await _patch(client, ctx, appointment_id, status="arrived")).status_code == 200

    summary = await client.get(f"{API}/clinic/reports/summary", headers=ctx.headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["appointments"]["arrived"] >= 1
