"""Тесты файлов пациента: снимки и документы в карте.

Файлы лежат на диске, а раздаются только через ручку — значит проверять надо не
«сохранилось ли», а границы:

* **чужой пациент — 404**, как несуществующий: иначе перебором id подтверждается,
  что человек лечится в соседней клинике;
* **тип определяется по содержимому**: `image/jpeg` в заголовке с чем угодно
  внутри — это то, что потом скачает сотрудник;
* **лимит размера считается по мере чтения**, а не по Content-Length;
* удаление мягкое: снимок — клинические данные;
* файл видят **все клиники пациента**, а правит и удаляет **только загрузившая**;
* архив КТ должен содержать DICOM и не содержать программ.

Хранилище на время теста подменяется на временный каталог — иначе тесты гадят в
рабочую папку проекта.
"""

from __future__ import annotations

import io
import itertools
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.modules.patients.application.use_cases import manage_patient_files
from app.shared.storage import files as storage_files
from tests.conftest import API, otp_login, own_membership_id, working_slot

_phone_seq = itertools.count(1)

# Минимальные валидные файлы: важно начало — по нему и определяется тип.
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_PDF = b"%PDF-1.7\n" + b"0" * 64
# DICOM: 128 байт преамбулы и «DICM».
_DICOM = b"\x00" * 128 + b"DICM" + b"\x02\x00" * 32


def _zip(entries: dict[str, bytes]) -> bytes:
    """ZIP-архив в памяти — как экспорт исследования КТ с диска."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


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
            "doctorMembershipId": await own_membership_id(client, headers),
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
    *,
    patient: str | None = None,
    **data: Any,
) -> Any:
    return await client.post(
        f"{API}/clinic/patients/{patient or ctx['patient']}/files",
        headers=ctx["headers"],
        files={"file": (name, content, mime)},
        data=data,
    )


async def _treat(client: AsyncClient, ctx: dict[str, Any], patient_id: str) -> None:
    """Клиника ``ctx`` начинает лечить чужого (глобального) пациента — запись к врачу."""
    appointment = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx["headers"],
        json={
            "patientId": patient_id,
            "doctorMembershipId": await own_membership_id(client, ctx["headers"]),
            "scheduledAt": working_slot(),
        },
    )
    assert appointment.status_code == 201, appointment.text


async def _files(
    client: AsyncClient, ctx: dict[str, Any], patient_id: str | None = None, **params: Any
) -> Any:
    listed = await client.get(
        f"{API}/clinic/patients/{patient_id or ctx['patient']}/files",
        headers=ctx["headers"],
        params=params,
    )
    assert listed.status_code == 200, listed.text
    return listed.json()


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


# ── 5. Зубы и дата снимка ───────────────────────────────────────────────────


async def test_file_is_attached_to_teeth_and_found_by_tooth(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Файл зуба находится отбором по зубу, общий — отбором «только общие».

    Номера приводятся к порядку и без повторов: приложение может прислать зубы
    в порядке нажатия.
    """
    ctx = await _clinic(admin_client, client, "Клиника Зубов")
    general = await _upload(client, ctx, _PNG, name="opg.png")
    tooth = await _upload(
        client, ctx, _JPEG, name="36.jpg", teeth=["37", "36", "36"], takenDate="2025-03-12"
    )
    assert general.status_code == 201, general.text
    assert tooth.status_code == 201, tooth.text
    assert general.json()["teeth"] == []
    assert tooth.json()["teeth"] == [36, 37]
    assert tooth.json()["takenDate"] == "2025-03-12"

    def ids(body: Any) -> list[str]:
        return [i["id"] for i in body["items"]]

    assert ids(await _files(client, ctx, tooth=36)) == [tooth.json()["id"]]
    assert ids(await _files(client, ctx, tooth=37)) == [tooth.json()["id"]]
    assert ids(await _files(client, ctx, tooth=11)) == []
    assert ids(await _files(client, ctx, general="true")) == [general.json()["id"]]
    # Свежие по дате СНИМКА: общий загружен сегодня, снимок зуба сделан в 2025-м.
    assert ids(await _files(client, ctx)) == [general.json()["id"], tooth.json()["id"]]


async def test_invalid_tooth_is_rejected_before_saving(
    admin_client: AsyncClient,
    client: AsyncClient,
    _storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Опечатка в номере зуба — 422 до записи в хранилище, а не после копирования КТ."""
    ctx = await _clinic(admin_client, client, "Клиника Опечаток")

    async def _must_not_save(*_: Any) -> Any:
        raise AssertionError("save_upload вызван до проверки зубов")

    monkeypatch.setattr(manage_patient_files, "save_upload", _must_not_save)

    resp = await _upload(client, ctx, _JPEG, teeth=["36", "19"])

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_tooth"
    assert not list(_storage.rglob("*.jpg"))


async def test_future_taken_date_is_rejected(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    ctx = await _clinic(admin_client, client, "Клиника Будущего")
    future = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()

    resp = await _upload(client, ctx, _JPEG, takenDate=future)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "taken_date_in_future"


async def test_list_filters_are_validated(admin_client: AsyncClient, client: AsyncClient) -> None:
    """Неверный зуб и «зуб + только общие» одновременно — 422, а не пустой список."""
    ctx = await _clinic(admin_client, client, "Клиника Фильтров")
    url = f"{API}/clinic/patients/{ctx['patient']}/files"

    wrong = await client.get(url, headers=ctx["headers"], params={"tooth": 19})
    both = await client.get(url, headers=ctx["headers"], params={"tooth": 36, "general": "true"})

    assert wrong.status_code == 422, wrong.text
    assert wrong.json()["error"] == "invalid_tooth"
    assert both.status_code == 422, both.text
    assert both.json()["error"] == "conflicting_filters"


# ── 6. Файлы других клиник ──────────────────────────────────────────────────


async def test_other_clinics_files_are_separate_and_marked(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """По умолчанию — свои файлы и счётчик чужих; чужие — по ``source=others``.

    Пациент глобальный: КТ, сделанная в одной клинике, нужна врачу другой. Но
    смешивать их в одну кучу нельзя — у каждого файла видно, чей он.
    """
    first = await _clinic(admin_client, client, "Клиника Первая")
    second = await _clinic(admin_client, client, "Клиника Вторая")
    patient = first["patient"]
    await _treat(client, second, patient)

    theirs = await _upload(client, first, _JPEG, name="kt.jpg", teeth=["36"])
    mine = await _upload(client, second, _PNG, name="foto.png", patient=patient)
    assert theirs.status_code == 201, theirs.text
    assert mine.status_code == 201, mine.text

    own = await _files(client, second, patient)
    assert [i["id"] for i in own["items"]] == [mine.json()["id"]]
    assert own["items"][0]["isOwn"] is True
    assert own["items"][0]["clinicName"] == "Клиника Вторая"
    assert own["othersCount"] == 1

    others = await _files(client, second, patient, source="others")
    assert [i["id"] for i in others["items"]] == [theirs.json()["id"]]
    assert others["items"][0]["isOwn"] is False
    assert others["items"][0]["clinicName"] == "Клиника Первая"

    every = await _files(client, second, patient, source="all")
    assert {i["id"] for i in every["items"]} == {theirs.json()["id"], mine.json()["id"]}

    # Счётчик чужих учитывает тот же отбор: у зуба 36 своих нет, чужой один.
    tooth = await _files(client, second, patient, tooth=36)
    assert tooth["items"] == []
    assert tooth["othersCount"] == 1

    # Скачать чужой снимок можно — ради этого его и видно.
    downloaded = await client.get(
        f"{API}/clinic/patients/{patient}/files/{theirs.json()['id']}",
        headers=second["headers"],
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == _JPEG


async def test_only_uploader_clinic_can_edit_or_delete(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Чужой файл — 403 и на правку, и на удаление; своя клиника правит свободно.

    Иначе любая клиника, к которой пациент однажды зашёл, стирала бы чужую КТ.
    """
    first = await _clinic(admin_client, client, "Клиника Хозяйка")
    second = await _clinic(admin_client, client, "Клиника Гостья")
    patient = first["patient"]
    await _treat(client, second, patient)
    uploaded = await _upload(client, first, _JPEG)
    url = f"{API}/clinic/patients/{patient}/files/{uploaded.json()['id']}"

    patched = await client.patch(url, headers=second["headers"], json={"comment": "чужое"})
    deleted = await client.delete(url, headers=second["headers"])

    assert patched.status_code == 403, patched.text
    assert patched.json()["error"] == "file_not_owned"
    assert deleted.status_code == 403, deleted.text
    assert deleted.json()["error"] == "file_not_owned"

    own = await client.patch(url, headers=first["headers"], json={"comment": "своё"})
    assert own.status_code == 200, own.text
    assert own.json()["comment"] == "своё"
    still_there = await _files(client, second, patient, source="others")
    assert [i["id"] for i in still_there["items"]] == [uploaded.json()["id"]]


async def test_patch_changes_comment_teeth_and_date(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """PATCH меняет только присланное; ``teeth: []`` делает файл общим, ``null`` — 422."""
    ctx = await _clinic(admin_client, client, "Клиника Правок")
    uploaded = await _upload(client, ctx, _JPEG, comment="Старое", teeth=["11"])
    url = f"{API}/clinic/patients/{ctx['patient']}/files/{uploaded.json()['id']}"

    changed = await client.patch(
        url,
        headers=ctx["headers"],
        json={"comment": "Снимок после пломбы", "teeth": [46, 45], "takenDate": "2026-01-10"},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["comment"] == "Снимок после пломбы"
    assert body["teeth"] == [45, 46]
    assert body["takenDate"] == "2026-01-10"
    assert body["isOwn"] is True

    # Только дата — описание и зубы не трогаются.
    dated = await client.patch(url, headers=ctx["headers"], json={"takenDate": None})
    assert dated.json()["comment"] == "Снимок после пломбы"
    assert dated.json()["teeth"] == [45, 46]
    assert dated.json()["takenDate"] is None

    general = await client.patch(url, headers=ctx["headers"], json={"teeth": []})
    assert general.json()["teeth"] == []

    nulled = await client.patch(url, headers=ctx["headers"], json={"teeth": None})
    wrong = await client.patch(url, headers=ctx["headers"], json={"teeth": [99]})
    assert nulled.status_code == 422, nulled.text
    assert wrong.status_code == 422, wrong.text
    assert wrong.json()["error"] == "invalid_tooth"

    journal = await client.get(f"{API}/clinic/audit", headers=ctx["headers"], params={"limit": 100})
    assert "patient.file_update" in [i["action"] for i in journal.json()["items"]]


async def test_patient_card_counts_files_per_tooth(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Карточка отдаёт счётчики файлов: свои и чужие, всего и по зубам.

    По ним приложение ставит метку у зуба в формуле, не загружая сами файлы.
    """
    first = await _clinic(admin_client, client, "Клиника Счёта")
    second = await _clinic(admin_client, client, "Клиника Соседа")
    patient = first["patient"]
    await _treat(client, second, patient)
    await _upload(client, first, _JPEG, teeth=["36", "37"])
    await _upload(client, first, _JPEG, teeth=["36"])
    await _upload(client, first, _PNG)
    await _upload(client, second, _JPEG, patient=patient, teeth=["36"])

    card = await client.get(f"{API}/clinic/patients/{patient}", headers=first["headers"])

    assert card.status_code == 200, card.text
    assert card.json()["files"] == {
        "own": 3,
        "others": 1,
        "teeth": [
            {"tooth": 36, "own": 2, "others": 1},
            {"tooth": 37, "own": 1, "others": 0},
        ],
    }


# ── 7. КТ: DICOM и ZIP-архив исследования ───────────────────────────────────


async def test_dicom_file_is_stored_as_ct(
    admin_client: AsyncClient, client: AsyncClient, _storage: Path
) -> None:
    """Одиночный DICOM опознаётся по метке на 128-м байте и ложится в ct/."""
    ctx = await _clinic(admin_client, client, "Клиника DICOM")

    resp = await _upload(client, ctx, _DICOM, name="srez.dcm", mime="application/octet-stream")

    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "ct"
    assert resp.json()["mime"] == "application/dicom"
    assert list((_storage / "ct").rglob("*.dcm"))


async def test_ct_zip_with_dicom_is_accepted(
    admin_client: AsyncClient, client: AsyncClient, _storage: Path
) -> None:
    """Архив исследования с DICOMDIR и срезами принимается как есть, без распаковки."""
    ctx = await _clinic(admin_client, client, "Клиника Архивов")
    archive = _zip({"DICOMDIR": _DICOM, "IMG/0001": _DICOM, "readme.txt": b"study"})

    resp = await _upload(client, ctx, archive, name="kt.zip", mime="application/zip")

    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "ct"
    assert resp.json()["mime"] == "application/zip"
    stored = list((_storage / "ct").rglob("*.zip"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == archive


@pytest.mark.parametrize(
    ("entries", "error"),
    [
        ({"photo.jpg": _JPEG}, "archive_without_dicom"),
        ({"IMG/0001": _DICOM, "Viewer/RadiAnt.exe": b"MZ\x90\x00"}, "archive_has_executable"),
    ],
    ids=["no-dicom", "viewer-inside"],
)
async def test_bad_ct_zip_is_rejected_and_removed(
    admin_client: AsyncClient,
    client: AsyncClient,
    _storage: Path,
    entries: dict[str, bytes],
    error: str,
) -> None:
    """Архив без DICOM или с программой внутри не принимается и не остаётся на диске.

    Программа просмотра с диска КТ — обычное дело, но этот архив откроет
    сотрудник другой клиники: исполняемый файл в нём — способ разнести заразу.
    """
    ctx = await _clinic(admin_client, client, f"Клиника {error}")

    resp = await _upload(client, ctx, _zip(entries), name="kt.zip", mime="application/zip")

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == error
    assert not list(_storage.rglob("*.zip"))


async def test_broken_zip_is_rejected(
    admin_client: AsyncClient, client: AsyncClient, _storage: Path
) -> None:
    ctx = await _clinic(admin_client, client, "Клиника Битых Архивов")

    resp = await _upload(
        client, ctx, b"PK\x03\x04" + b"\x00" * 256, name="kt.zip", mime="application/zip"
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_archive"
    assert not list(_storage.rglob("*.zip"))


async def test_ct_has_its_own_size_limit(
    admin_client: AsyncClient, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """У КТ свой потолок: общий лимит снимков его не режет, а свой — режет."""
    monkeypatch.setattr(settings, "upload_max_bytes", 1024)
    monkeypatch.setattr(settings, "upload_ct_max_bytes", 16 * 1024)
    ctx = await _clinic(admin_client, client, "Клиника Лимитов КТ")
    big_dicom = _DICOM + b"\x00" * 4096

    photo = await _upload(client, ctx, _JPEG + b"\x00" * 4096)
    ct = await _upload(client, ctx, big_dicom, name="srez.dcm")
    assert photo.status_code == 422, photo.text
    assert photo.json()["error"] == "file_too_large"
    assert ct.status_code == 201, ct.text

    monkeypatch.setattr(settings, "upload_ct_max_bytes", 1024)
    too_big = await _upload(client, ctx, big_dicom, name="srez.dcm")
    assert too_big.status_code == 422, too_big.text
    assert too_big.json()["error"] == "file_too_large"


def _encrypted(archive: bytes) -> bytes:
    """Пометить записи архива зашифрованными (бит 0 флагов в оглавлении).

    ``zipfile`` шифровать не умеет, а проверка смотрит именно на флаг в
    оглавлении — его и выставляем.
    """
    data = bytearray(archive)
    pos = data.find(b"PK\x01\x02")
    while pos >= 0:
        data[pos + 8] |= 0x1
        pos = data.find(b"PK\x01\x02", pos + 4)
    return bytes(data)


async def test_ct_zip_safety_checks(
    admin_client: AsyncClient,
    client: AsyncClient,
    _storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Архивы, которые нельзя проверить безопасно, отвергаются и не остаются на диске.

    * bzip2/lzma ``zipfile`` разжимает без предела — 4 КБ превращаются в
      гигабайты, поэтому принимаем только stored/deflate;
    * число записей проверяется до разбора оглавления (здесь потолок занижен);
    * архив под паролем не проверить вовсе.
    """
    ctx = await _clinic(admin_client, client, "Клиника Безопасных Архивов")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_BZIP2) as archive:
        archive.writestr("IMG/0001", _DICOM)
    cases = {
        "archive_unsupported_compression": buffer.getvalue(),
        "archive_encrypted": _encrypted(_zip({"IMG/0001": _DICOM})),
        "archive_too_many_entries": _zip({f"IMG/{i:04d}": _DICOM for i in range(3)}),
    }
    monkeypatch.setattr(storage_files, "_ZIP_MAX_ENTRIES", 2)

    for error, content in cases.items():
        resp = await _upload(client, ctx, content, name="kt.zip", mime="application/zip")
        assert resp.status_code == 422, (error, resp.text)
        assert resp.json()["error"] == error
    assert not list(_storage.rglob("*.zip"))


@pytest.mark.parametrize(
    "entry",
    [
        "Viewer.app/Contents/MacOS/Viewer",
        "viewer.exe.",
        "viewer.exe ",
        "tools/viewer.exe::$DATA",
        "nested/second.zip",
    ],
    ids=["mac-bundle", "trailing-dot", "trailing-space", "ads-stream", "nested-zip"],
)
async def test_executable_names_cannot_hide_in_ct_zip(
    admin_client: AsyncClient, client: AsyncClient, entry: str
) -> None:
    """Программа не проходит под именем, которое Windows или macOS «дочистят» до запуска."""
    ctx = await _clinic(admin_client, client, f"Клиника Имён {entry[:12]}")

    resp = await _upload(
        client,
        ctx,
        _zip({"IMG/0001": _DICOM, entry: b"payload"}),
        name="kt.zip",
        mime="application/zip",
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "archive_has_executable"


async def test_executable_with_dicom_mark_is_rejected(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Программа с меткой DICM в «преамбуле» (CVE-2019-11687) не принимается как снимок."""
    ctx = await _clinic(admin_client, client, "Клиника Полиглотов")
    polyglot = b"MZ" + b"\x00" * 126 + b"DICM" + b"\x00" * 64

    resp = await _upload(client, ctx, polyglot, name="srez.dcm")

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "unsupported_file_type"


async def test_download_name_uses_detected_extension(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Скачивается с расширением по настоящему типу: ``viewer.exe`` с DICOM — это ``.dcm``."""
    ctx = await _clinic(admin_client, client, "Клиника Имён Скачивания")
    uploaded = await _upload(client, ctx, _DICOM, name="viewer.exe")
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["originalName"] == "viewer.exe"

    downloaded = await client.get(
        f"{API}/clinic/patients/{ctx['patient']}/files/{uploaded.json()['id']}",
        headers=ctx["headers"],
    )

    assert downloaded.status_code == 200, downloaded.text
    disposition = downloaded.headers["content-disposition"]
    assert "viewer.dcm" in disposition
    assert ".exe" not in disposition


# ── 8. Отсев до приёма тела ─────────────────────────────────────────────────


async def test_upload_without_valid_token_is_rejected_before_body(
    admin_client: AsyncClient,
    client: AsyncClient,
    public_client: AsyncClient,
    _storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без токена или с поддельным — 401 до разбора формы: КТ не ложится во временный каталог.

    Что отказ дал именно фильтр, а не зависимость ручки, видно по ручке: она
    подменена так, что любой вызов роняет тест.
    """
    ctx = await _clinic(admin_client, client, "Клиника Анонимов")
    url = f"{API}/clinic/patients/{ctx['patient']}/files"
    files = {"file": ("kt.dcm", _DICOM, "application/dicom")}

    async def _must_not_parse(*_: Any) -> Any:
        raise AssertionError("тело формы разобрано до проверки токена")

    monkeypatch.setattr(storage_files, "save_upload", _must_not_parse)
    monkeypatch.setattr(manage_patient_files, "save_upload", _must_not_parse)

    anonymous = await public_client.post(url, files=files)
    forged = await public_client.post(
        url, files=files, headers={"Authorization": "Bearer forged.token.value"}
    )

    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json()["error"] == "unauthorized"
    assert forged.status_code == 401, forged.text
    assert forged.json()["error"] == "invalid_token"
    assert not list(_storage.rglob("*.dcm"))


async def test_oversized_content_length_is_rejected_before_body(
    admin_client: AsyncClient,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Content-Length`` больше лимита КТ — 413 сразу; 413 отдаёт только фильтр."""
    monkeypatch.setattr(settings, "upload_ct_max_bytes", 1024)
    ctx = await _clinic(admin_client, client, "Клиника Гигантов")

    resp = await _upload(client, ctx, _DICOM + b"\x00" * (2 * 1024 * 1024), name="kt.dcm")

    assert resp.status_code == 413, resp.text
    assert resp.json()["error"] == "file_too_large"


# ── 9. Границы правки ───────────────────────────────────────────────────────


async def test_patch_and_delete_of_other_patients_file_are_404(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Свой пациент в пути + id файла другого пациента — 404, файл не трогается."""
    ctx = await _clinic(admin_client, client, "Клиника Подмены")
    other = await client.post(
        f"{API}/clinic/patients",
        headers=ctx["headers"],
        json={"phone": _phone(), "firstName": "Второй", "lastName": "Пациент"},
    )
    await _treat(client, ctx, other.json()["id"])
    foreign = await _upload(client, ctx, _JPEG, patient=other.json()["id"], comment="Как было")
    url = f"{API}/clinic/patients/{ctx['patient']}/files/{foreign.json()['id']}"

    patched = await client.patch(url, headers=ctx["headers"], json={"comment": "подмена"})
    deleted = await client.delete(url, headers=ctx["headers"])

    assert patched.status_code == 404, patched.text
    assert deleted.status_code == 404, deleted.text
    intact = await _files(client, ctx, other.json()["id"])
    assert [i["comment"] for i in intact["items"]] == ["Как было"]


async def test_clinic_not_treating_patient_cannot_patch(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Клиника, которая пациента не лечит, получает 404, как на несуществующего."""
    owner = await _clinic(admin_client, client, "Клиника Лечащая")
    stranger = await _clinic(admin_client, client, "Клиника Посторонняя")
    uploaded = await _upload(client, owner, _JPEG)

    resp = await client.patch(
        f"{API}/clinic/patients/{owner['patient']}/files/{uploaded.json()['id']}",
        headers=stranger["headers"],
        json={"comment": "чужое"},
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "patient_not_found"


async def test_worker_without_edit_right_can_read_but_not_change(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """У «Работника» есть ``patients.view_medical``, но нет ``patients.edit``."""
    ctx = await _clinic(admin_client, client, "Клиника Работников")
    uploaded = await _upload(client, ctx, _JPEG)
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    added = await client.post(
        f"{API}/clinic/staff",
        headers=ctx["headers"],
        json={"fullName": "Работник Файлов", "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    worker = {**ctx, "headers": await otp_login(client, phone)}
    url = f"{API}/clinic/patients/{ctx['patient']}/files/{uploaded.json()['id']}"

    assert [i["id"] for i in (await _files(client, worker))["items"]] == [uploaded.json()["id"]]
    patched = await client.patch(url, headers=worker["headers"], json={"comment": "правка"})
    deleted = await client.delete(url, headers=worker["headers"])
    posted = await _upload(client, worker, _PNG)

    assert patched.status_code == 403, patched.text
    assert deleted.status_code == 403, deleted.text
    assert posted.status_code == 403, posted.text


async def test_deleted_files_are_not_counted(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Мягко удалённый файл не попадает ни в счётчики карточки, ни в ``othersCount``."""
    first = await _clinic(admin_client, client, "Клиника Удаляющая")
    second = await _clinic(admin_client, client, "Клиника Смотрящая")
    patient = first["patient"]
    await _treat(client, second, patient)
    kept = await _upload(client, first, _JPEG, teeth=["36"])
    gone = await _upload(client, first, _JPEG, teeth=["36", "37"])
    removed = await client.delete(
        f"{API}/clinic/patients/{patient}/files/{gone.json()['id']}", headers=first["headers"]
    )
    assert kept.status_code == 201, kept.text
    assert removed.status_code == 200, removed.text

    card = await client.get(f"{API}/clinic/patients/{patient}", headers=second["headers"])
    listed = await _files(client, second, patient)

    assert card.json()["files"] == {
        "own": 0,
        "others": 1,
        "teeth": [{"tooth": 36, "own": 0, "others": 1}],
    }
    assert listed["othersCount"] == 1
