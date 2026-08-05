"""API-тесты жизненного цикла приёма: воронка, откат статуса, причина отмены.

Воронка: ``draft → scheduled → confirmed → arrived → in_progress → completed``
плюс ``cancelled`` (с причиной из справочника) и ``no_show``.

Ключевые риски:

* завершённый приём не должен «расзавершаться» — за ним стоят платежи кассы;
* откат должен быть узким и подотчётным: один шаг назад, с правом, только для
  приёма текущего дня, с комментарием и записью в аудит. Иначе появляется способ
  тихо править статистику прошедших дней;
* причина отмены обязательна и из справочника — иначе отмены нечем считать;
* причина не должна оставаться висеть на приёме, у которого отмену сняли;
* отмена не должна оставлять сумму: иначе прерванный приём закроют как
  ``completed`` с нулём, и отчёту нельзя верить;
* приём без позиций не должен закрываться ни ручкой завершения, ни PATCH —
  нулевые чеки копятся и портят среднюю выручку.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, time, timedelta
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


def _slot(days_ago: int = 0) -> str:
    """Уникальный слот в рабочем окне; ``days_ago`` — сдвиг в прошлое."""
    index = next(_slot_seq)
    hour = (9, 10, 11, 14, 15, 16, 17)[index % 7]
    minute = 30 * ((index // 7) % 2)
    day = datetime.now(BISHKEK).date() - timedelta(days=days_ago)
    return datetime.combine(day, time(hour, minute), tzinfo=BISHKEK).isoformat()


def _hours_all_week() -> dict[str, Any]:
    """Часы работы на все семь дней — чтобы тесты не зависели от дня недели."""
    return {
        "days": [
            {
                "weekday": d,
                "opensAt": "08:00:00",
                "closesAt": "20:00:00",
                "breakStart": None,
                "breakEnd": None,
                "isWorking": True,
            }
            for d in range(7)
        ]
    }


@dataclass(slots=True)
class ClinicCtx:
    headers: dict[str, str]
    patient_id: str
    worker_headers: dict[str, str]  # врач: правит приёмы, но откатывать не может
    service_id: str  # услуга на 3000 тыйынов — позиция приёма


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
    await client.put(
        f"{API}/clinic/schedule/working-hours", headers=headers, json=_hours_all_week()
    )
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Статусов"},
    )
    assert patient.status_code == 201, patient.text
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 3000}
    )
    assert service.status_code == 201, service.text

    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    worker_phone = _phone()
    worker = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Врач", "phone": worker_phone, "specializationId": spec},
    )
    assert worker.status_code == 201, worker.text
    worker_headers = await _login(client, worker_phone, str(worker.json()["tempPassword"]))

    return ClinicCtx(
        headers=headers,
        patient_id=str(patient.json()["id"]),
        worker_headers=worker_headers,
        service_id=str(service.json()["id"]),
    )


async def _book(client: AsyncClient, ctx: ClinicCtx, **extra: Any) -> str:
    """Приём с одной позицией (зуб 46 по услуге) — без позиций его не закрыть."""
    body: dict[str, Any] = {
        "patientId": ctx.patient_id,
        "scheduledAt": _slot(),
        "items": [{"serviceId": ctx.service_id, "teeth": [46]}],
        **extra,
    }
    resp = await client.post(f"{API}/clinic/appointments", headers=ctx.headers, json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _patch(
    client: AsyncClient,
    ctx: ClinicCtx,
    appointment_id: str,
    *,
    headers: dict[str, str] | None = None,
    **body: Any,
) -> Any:
    return await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=headers or ctx.headers,
        json=body,
    )


# ── 1. Движение вперёд ──────────────────────────────────────────────────────


async def test_full_lifecycle(client: AsyncClient, ctx: ClinicCtx) -> None:
    """scheduled → confirmed → arrived → in_progress → completed проходит целиком."""
    appointment_id = await _book(client, ctx)
    for status in ("confirmed", "arrived", "in_progress", "completed"):
        resp = await _patch(client, ctx, appointment_id, status=status)
        assert resp.status_code == 200, f"{status}: {resp.text}"
        assert resp.json()["status"] == status


async def test_forward_needs_no_comment_or_right(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Шаг вперёд — обычная работа: ни права на откат, ни комментария не нужно."""
    appointment_id = await _book(client, ctx)
    resp = await _patch(client, ctx, appointment_id, headers=ctx.worker_headers, status="arrived")
    assert resp.status_code == 200, resp.text


# ── 2. Откат: узкий и подотчётный ───────────────────────────────────────────


async def test_rollback_one_step_back_is_audited(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Один шаг назад с комментарием проходит и попадает в аудит."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="arrived")

    back = await _patch(
        client, ctx, appointment_id, status="confirmed", rollbackComment="Ошиблись кнопкой"
    )
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "confirmed"

    audit = await admin_client.get(f"{API}/admin/audit")
    assert audit.status_code == 200, audit.text
    assert "appointment.status_rollback" in audit.text
    assert "Ошиблись кнопкой" in audit.text


async def test_rollback_without_comment_is_422(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Откат без комментария не проходит — иначе исправления не отследить."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="arrived")

    resp = await _patch(client, ctx, appointment_id, status="confirmed")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "rollback_comment_required"


async def test_rollback_needs_permission(client: AsyncClient, ctx: ClinicCtx) -> None:
    """У врача есть appointments.edit, но откатывать статус он не может."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="arrived")

    resp = await _patch(
        client,
        ctx,
        appointment_id,
        headers=ctx.worker_headers,
        status="confirmed",
        rollbackComment="Верну назад",
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "status_rollback_forbidden"


async def test_rollback_only_for_today(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Приём прошлого дня откатить нельзя — это правка истории задним числом."""
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": ctx.patient_id, "scheduledAt": _slot(days_ago=3)},
    )
    assert resp.status_code == 201, resp.text
    appointment_id = str(resp.json()["id"])
    await _patch(client, ctx, appointment_id, status="arrived")

    back = await _patch(
        client, ctx, appointment_id, status="confirmed", rollbackComment="Поправить"
    )
    assert back.status_code == 422, back.text
    assert back.json()["error"] == "rollback_not_today"


async def test_rollback_two_steps_is_forbidden(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Откат — ровно один шаг: из «в кресле» сразу в «ожидает» нельзя."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="in_progress")

    resp = await _patch(
        client, ctx, appointment_id, status="scheduled", rollbackComment="Слишком далеко"
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "invalid_status_transition"


async def test_no_show_rollback_to_arrived(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Опоздал, но пришёл — ежедневный случай: из неявки возвращаем в «пришёл»."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="no_show")

    back = await _patch(
        client, ctx, appointment_id, status="arrived", rollbackComment="Опоздал, но пришёл"
    )
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "arrived"


# ── 3. Защита завершённого приёма ───────────────────────────────────────────


@pytest.mark.parametrize(
    "target", ["scheduled", "confirmed", "arrived", "in_progress", "cancelled"]
)
async def test_completed_is_terminal(client: AsyncClient, ctx: ClinicCtx, target: str) -> None:
    """Завершённый приём не меняет статус: ошибку исправляют возвратом платежа."""
    appointment_id = await _book(client, ctx)
    assert (await _patch(client, ctx, appointment_id, status="completed")).status_code == 200

    resp = await _patch(
        client,
        ctx,
        appointment_id,
        status=target,
        rollbackComment="Пробуем откатить",
        cancelReason="duplicate",
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "invalid_status_transition"


async def test_completed_stays_completed_after_rejected_transition(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Отклонённый переход ничего не портит — статус на месте."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="completed")
    await _patch(client, ctx, appointment_id, status="cancelled", cancelReason="duplicate")

    card = await client.get(f"{API}/clinic/appointments/{appointment_id}", headers=ctx.headers)
    assert card.json()["status"] == "completed"


# ── 4. Черновик ─────────────────────────────────────────────────────────────


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
    """Передумавшего пациента закрывают отменой прямо из черновика."""
    created = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": ctx.patient_id, "status": "draft"},
    )
    resp = await _patch(
        client, ctx, created.json()["id"], status="cancelled", cancelReason="patient_request"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["cancelReason"] == "patient_request"


# ── 5. Причина отмены ───────────────────────────────────────────────────────


async def test_cancel_requires_reason(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Без причины отменить нельзя — иначе отмены нечем считать."""
    appointment_id = await _book(client, ctx)
    resp = await _patch(client, ctx, appointment_id, status="cancelled")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "cancel_reason_required"


async def test_other_reason_requires_comment(client: AsyncClient, ctx: ClinicCtx) -> None:
    """«Иное» без пояснения не несёт информации."""
    appointment_id = await _book(client, ctx)
    resp = await _patch(client, ctx, appointment_id, status="cancelled", cancelReason="other")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "cancel_comment_required"

    ok = await _patch(
        client,
        ctx,
        appointment_id,
        status="cancelled",
        cancelReason="other",
        cancelComment="Сломалось оборудование",
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["cancelComment"] == "Сломалось оборудование"


async def test_unknown_reason_is_422(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Причина — справочник: произвольный текст не принимается."""
    appointment_id = await _book(client, ctx)
    resp = await _patch(
        client, ctx, appointment_id, status="cancelled", cancelReason="просто так"
    )
    assert resp.status_code == 422, resp.text


async def test_cancel_reason_cleared_when_uncancelled(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """При снятии отмены повод очищается — он больше не относится к приёму."""
    appointment_id = await _book(client, ctx)
    cancelled = await _patch(
        client, ctx, appointment_id, status="cancelled", cancelReason="doctor_unavailable"
    )
    assert cancelled.status_code == 200, cancelled.text

    restored = await _patch(
        client, ctx, appointment_id, status="scheduled", rollbackComment="Врач освободился"
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["cancelReason"] is None
    assert restored.json()["cancelComment"] is None


async def test_cancel_reason_without_cancel_is_422(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Причина без отмены — ошибка: иначе она повиснет на рабочем приёме."""
    appointment_id = await _book(client, ctx)
    resp = await _patch(client, ctx, appointment_id, cancelReason="duplicate")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "cancel_reason_without_cancel"


async def test_cancel_from_in_progress(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Начатый приём можно прервать — с обязательной причиной."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="in_progress")
    resp = await _patch(
        client, ctx, appointment_id, status="cancelled", cancelReason="patient_illness"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


# ── 6. Отмена обнуляет сумму, позиции остаются ──────────────────────────────


async def test_cancel_zeroes_total_but_keeps_items(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Прерванный приём не тарифицируется, но что успели сделать — остаётся в истории.

    Иначе такие случаи закрывают как completed с нулём — то есть врут отчёту.
    """
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="in_progress")

    resp = await _patch(
        client, ctx, appointment_id, status="cancelled", cancelReason="patient_illness"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["priceTotalTiyin"] == 0
    assert len(resp.json()["items"]) == 1


async def test_uncancel_restores_total_from_items(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Снятие отмены возвращает сумму — и считает её из позиций, а не «помнит»."""
    appointment_id = await _book(client, ctx)
    await _patch(client, ctx, appointment_id, status="cancelled", cancelReason="duplicate")

    restored = await _patch(
        client, ctx, appointment_id, status="scheduled", rollbackComment="Ошиблись приёмом"
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["priceTotalTiyin"] == 3000


# ── 7. Закрытие приёма: позиции или явное «бесплатно» ───────────────────────


async def _book_empty(client: AsyncClient, ctx: ClinicCtx) -> str:
    """Приём без позиций — тот самый «нулевой чек из незаполненной формы»."""
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": ctx.patient_id, "scheduledAt": _slot()},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _complete(client: AsyncClient, ctx: ClinicCtx, appointment_id: str, **body: Any) -> Any:
    return await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete",
        headers=ctx.headers,
        json=body or None,
    )


async def test_complete_without_items_is_rejected(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Приём без позиций не закрывается: нулевые чеки портят среднюю выручку."""
    resp = await _complete(client, ctx, await _book_empty(client, ctx))
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "appointment_without_items"


async def test_no_charge_requires_comment(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Голый флаг «бесплатно» — тот же нулевой чек, только с галочкой."""
    resp = await _complete(client, ctx, await _book_empty(client, ctx), noCharge=True)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "no_charge_comment_required"


async def test_no_charge_closes_empty_appointment(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Бесплатный приём бывает — но он решение с пояснением."""
    resp = await _complete(
        client,
        ctx,
        await _book_empty(client, ctx),
        noCharge=True,
        noChargeComment="Гарантийный осмотр",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["noCharge"] is True
    assert body["noChargeComment"] == "Гарантийный осмотр"
    assert body["priceTotalTiyin"] == 0


async def test_no_charge_with_items_is_rejected(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Приём с позициями флагом не обнуляется — это была бы скидка 100% мимо права."""
    resp = await _complete(
        client, ctx, await _book(client, ctx), noCharge=True, noChargeComment="Бесплатно"
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "no_charge_with_items"


async def test_patch_to_completed_without_items_is_rejected(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """PATCH — не дыра в обход правила: закрыть пустой приём им тоже нельзя."""
    resp = await _patch(client, ctx, await _book_empty(client, ctx), status="completed")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "appointment_without_items"


async def test_no_charge_set_earlier_lets_complete_pass(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Флаг можно проставить заранее — тогда повторять его в завершении не нужно."""
    appointment_id = await _book_empty(client, ctx)
    marked = await _patch(
        client, ctx, appointment_id, noCharge=True, noChargeComment="Консультация без назначений"
    )
    assert marked.status_code == 200, marked.text

    resp = await _complete(client, ctx, appointment_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"


async def test_no_charge_comment_without_flag_is_422(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Пояснение живёт только вместе с флагом — как и причина отмены."""
    resp = await _patch(
        client, ctx, await _book_empty(client, ctx), noChargeComment="Просто так"
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "no_charge_comment_without_flag"


# ── 8. Отчёты ───────────────────────────────────────────────────────────────


async def test_arrived_counted_in_reports(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Новый статус виден в отчёте по приёмам отдельным счётчиком."""
    appointment_id = await _book(client, ctx)
    assert (await _patch(client, ctx, appointment_id, status="arrived")).status_code == 200

    summary = await client.get(f"{API}/clinic/reports/summary", headers=ctx.headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["appointments"]["arrived"] >= 1
