"""Тесты файлов пациента: снимки и документы в карте.

Файлы лежат на диске, а раздаются только через ручку — значит проверять надо не
«сохранилось ли», а границы:

* **чужой пациент — 404**, как несуществующий: иначе перебором id подтверждается,
  что человек лечится в соседней клинике;
* **тип определяется по содержимому**: `image/jpeg` в заголовке с чем угодно
  внутри — это то, что потом скачает сотрудник;
* **лимит размера считается по мере чтения**, а не по Content-Length;
* удаление мягкое: снимок — клинические данные.

Хранилище на время теста подменяется на временный каталог — иначе тесты гадят в
рабочую папку проекта.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests.conftest import API, otp_login, working_slot

_phone_seq = itertools.count(1)

# Минимальные валидные файлы: важно начало — по нему и определяется тип.
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_PDF = b"%PDF-1.7\n" + b"0" * 64


def _phone() -> str:
    return f"070086{next(_phone_seq):04d}"


@pytest.fixture(autouse=True)
def _storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Хранилище — во временный каталог теста, а не в рабочую папку проекта."""
    monkeypatch.setattr(settings, "storage_root", str(tmp_path / "storage"))
    return tmp_path / "storage"


async def _clinic(admin_client: AsyncClient, client: AsyncClient, name: str) -> dict[str, Any]:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Файлов",
            "adminPhone": phone,
            "name": name,
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)

    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Файлов"},
    )
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Снимок", "priceTiyin": 1000}
    )
    appointment = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": patient.json()["id"],
            "scheduledAt": working_slot(),
            "items": [{"serviceId": service.json()["id"], "teeth": [46]}],
        },
    )
    assert appointment.status_code == 201, appointment.text
    return {
        "headers": headers,
        "patient": patient.json()["id"],
        "appointment": appointment.json()["id"],
    }


async def _upload(
    client: AsyncClient,
    ctx: dict[str, Any],
    content: bytes,
    name: str = "snimok.jpg",
    mime: str = "image/jpeg",
    **data: str,
) -> Any:
    return await client.post(
        f"{API}/clinic/patients/{ctx['patient']}/files",
        headers=ctx["headers"],
        files={"file": (name, content, mime)},
        data=data,
    )


# ── 1. Загрузка и выдача ────────────────────────────────────────────────────


async def test_upload_and_download_roundtrip(
    admin_client: AsyncClient, client: AsyncClient, _storage: Path
) -> None:
    """Файл сохраняется на диск по датовой раскладке и скачивается обратно."""
    ctx = await _clinic(admin_client, client, "Клиника Снимков")

    uploaded = await _upload(client, ctx, _JPEG, name="panorama.jpg")

    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["kind"] == "images"
    assert body["mime"] == "image/jpeg"
    assert body["originalName"] == "panorama.jpg"
    assert body["sizeBytes"] == len(_JPEG)

    # На диске: storage/images/ГГГГ/ММ/ДД/<uuid>.jpg — имя генерируем мы сами.
    stored = list((_storage / "images").rglob("*.jpg"))
    assert len(stored) == 1
    assert stored[0].name != "panorama.jpg"
    assert stored[0].read_bytes() == _JPEG

    downloaded = await client.get(
        f"{API}/clinic/patients/{ctx['patient']}/files/{body['id']}", headers=ctx["headers"]
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == _JPEG
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in downloaded.headers["content-disposition"]


async def test_pdf_goes_to_files_directory(
    admin_client: AsyncClient, client: AsyncClient, _storage: Path
) -> None:
    """Документ ложится в files/, а не к снимкам."""
    ctx = await _clinic(admin_client, client, "Клиника Документов")

    uploaded = await _upload(client, ctx, _PDF, name="dogovor.pdf", mime="application/pdf")

    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["kind"] == "files"
    assert list((_storage / "files").rglob("*.pdf"))


async def test_file_can_be_linked_to_appointment(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Снимок привязывается к приёму и находится фильтром по нему."""
    ctx = await _clinic(admin_client, client, "Клиника Приёма")

    uploaded = await _upload(client, ctx, _PNG, name="zub.png", appointmentId=ctx["appointment"])
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["appointmentId"] == ctx["appointment"]

    listed = await client.get(
        f"{API}/clinic/patients/{ctx['patient']}/files",
        headers=ctx["headers"],
        params={"appointmentId": ctx["appointment"]},
    )
    assert [i["id"] for i in listed.json()["items"]] == [uploaded.json()["id"]]


# ── 2. Проверки содержимого ─────────────────────────────────────────────────


async def test_type_is_detected_by_content_not_by_header(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Исполняемый файл под видом снимка не проходит: тип берём из содержимого."""
    ctx = await _clinic(admin_client, client, "Клиника Подделок")

    resp = await _upload(client, ctx, b"MZ\x90\x00" + b"\x00" * 64, name="virus.jpg")

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "unsupported_file_type"


async def test_oversized_file_is_rejected_and_not_left_on_disk(
    admin_client: AsyncClient,
    client: AsyncClient,
    _storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Файл сверх лимита не принимается и не остаётся недописанным в хранилище."""
    monkeypatch.setattr(settings, "upload_max_bytes", 1024)
    ctx = await _clinic(admin_client, client, "Клиника Лимита")

    resp = await _upload(client, ctx, _JPEG[:4] + b"\x00" * 4096)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "file_too_large"
    assert not list(_storage.rglob("*.jpg"))


async def test_empty_file_is_rejected(admin_client: AsyncClient, client: AsyncClient) -> None:
    ctx = await _clinic(admin_client, client, "Клиника Пустых")

    resp = await _upload(client, ctx, b"")

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "empty_file"


# ── 3. Доступ ───────────────────────────────────────────────────────────────


async def test_other_clinic_patient_is_404(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Чужой пациент отвечает как несуществующий — и на список, и на загрузку."""
    mine = await _clinic(admin_client, client, "Клиника Своя")
    stranger = await _clinic(admin_client, client, "Клиника Чужая")

    listed = await client.get(
        f"{API}/clinic/patients/{stranger['patient']}/files", headers=mine["headers"]
    )
    assert listed.status_code == 404, listed.text
    assert listed.json()["error"] == "patient_not_found"

    uploaded = await client.post(
        f"{API}/clinic/patients/{stranger['patient']}/files",
        headers=mine["headers"],
        files={"file": ("chuzhoy.jpg", _JPEG, "image/jpeg")},
    )
    assert uploaded.status_code == 404, uploaded.text


async def test_file_of_other_patient_is_404(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Файл, принадлежащий другому пациенту, по этому пациенту не отдаётся."""
    first = await _clinic(admin_client, client, "Клиника Файл-1")
    second = await _clinic(admin_client, client, "Клиника Файл-2")
    uploaded = await _upload(client, second, _JPEG)

    resp = await client.get(
        f"{API}/clinic/patients/{first['patient']}/files/{uploaded.json()['id']}",
        headers=first["headers"],
    )

    assert resp.status_code == 404, resp.text


async def test_files_need_auth(client: AsyncClient) -> None:
    resp = await client.get(
        f"{API}/clinic/patients/00000000-0000-4000-8000-000000000000/files"
    )
    assert resp.status_code in (401, 403), resp.text


# ── 4. Удаление ─────────────────────────────────────────────────────────────


async def test_delete_hides_file_but_keeps_it_on_disk(
    admin_client: AsyncClient, client: AsyncClient, _storage: Path
) -> None:
    """Удаление мягкое: из карты пропал, с диска — нет.

    Снимок это клинические данные: «случайно удалил» не должно означать
    «потеряли навсегда».
    """
    ctx = await _clinic(admin_client, client, "Клиника Удалений")
    uploaded = await _upload(client, ctx, _JPEG)
    file_id = uploaded.json()["id"]

    deleted = await client.delete(
        f"{API}/clinic/patients/{ctx['patient']}/files/{file_id}", headers=ctx["headers"]
    )
    assert deleted.status_code == 200, deleted.text

    listed = await client.get(
        f"{API}/clinic/patients/{ctx['patient']}/files", headers=ctx["headers"]
    )
    assert listed.json()["items"] == []

    gone = await client.get(
        f"{API}/clinic/patients/{ctx['patient']}/files/{file_id}", headers=ctx["headers"]
    )
    assert gone.status_code == 404, gone.text
    assert list(_storage.rglob("*.jpg"))  # файл на месте


async def test_upload_and_delete_are_audited(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Работа с медданными видна в журнале клиники."""
    ctx = await _clinic(admin_client, client, "Клиника Журнала Файлов")
    uploaded = await _upload(client, ctx, _JPEG)
    await client.delete(
        f"{API}/clinic/patients/{ctx['patient']}/files/{uploaded.json()['id']}",
        headers=ctx["headers"],
    )

    journal = await client.get(
        f"{API}/clinic/audit", headers=ctx["headers"], params={"limit": 100}
    )
    actions = [i["action"] for i in journal.json()["items"]]

    assert "patient.file_upload" in actions
    assert "patient.file_delete" in actions
