"""Шаблоны напоминаний для Meta и скрипт, который их создаёт.

Текст шаблона хранит Meta, а сервер шлёт только значения по порядку и payload
кнопок. Поэтому проверяется главное: сколько переменных и кнопок в каждом
шаблоне — ровно столько, сколько шлют отправители, и Meta примет порядок.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.core.config import settings
from app.modules.whatsapp.application.services.template_copies import copied_template_names
from app.modules.whatsapp.domain.reminder_templates import KY, RU, reminder_templates
from app.scripts import wa_reminder_templates as script
from app.shared.whatsapp_cloud import MetaOnboardingError, TemplateDefinition

#: Сколько значений шлёт сервер: visits._template_for и patient_reminders.
_VALUES = {"visit_reminder": 4, "debt_reminder": 3, "recall_reminder": 3}
_BUTTONS = {
    ("visit_reminder", RU): ["Приду", "Перенести"],
    ("visit_reminder", KY): ["Келем", "Которуу"],
    ("recall_reminder", RU): ["Записаться", "Не сейчас"],
    ("recall_reminder", KY): ["Жазылуу", "Азыр эмес"],
}


def _templates(**overrides: Any) -> list[TemplateDefinition]:
    params: dict[str, Any] = {
        "visit_name": "visit_reminder",
        "debt_name": "debt_reminder",
        "recall_name": "recall_reminder",
        "visit_buttons": True,
        "recall_buttons": True,
        "recall_months": 6,
    }
    return reminder_templates(**{**params, **overrides})


def _component(template: TemplateDefinition, kind: str) -> dict[str, Any] | None:
    return next((c for c in template.components if c["type"] == kind), None)


def test_three_templates_on_two_languages() -> None:
    keys = {(t.name, t.language, t.category) for t in _templates()}
    assert keys == {
        (name, lang, category)
        for name, category in (
            ("visit_reminder", "UTILITY"),
            ("debt_reminder", "UTILITY"),
            ("recall_reminder", "MARKETING"),
        )
        for lang in (RU, KY)
    }


def test_variables_match_what_the_server_sends() -> None:
    for template in _templates():
        body = _component(template, "BODY")
        assert body is not None, template.name
        numbers = [int(n) for n in re.findall(r"\{\{(\d+)\}\}", body["text"])]
        expected = _VALUES[template.name]
        # По возрастанию, без пропусков и ровно столько, сколько значений шлёт сервер.
        assert numbers == list(range(1, expected + 1)), (template.name, template.language)
        assert len(body["example"]["body_text"][0]) == expected
        # Переменная в самом начале или конце текста — отказ Meta.
        text = body["text"].strip()
        assert not text.startswith("{{") and not text.endswith("}}"), template.name


def test_buttons_are_two_quick_replies_in_server_order() -> None:
    for template in _templates():
        buttons = _component(template, "BUTTONS")
        expected = _BUTTONS.get((template.name, template.language))
        if expected is None:
            assert buttons is None, template.name  # у долга кнопок нет
            continue
        assert buttons is not None
        assert [b["type"] for b in buttons["buttons"]] == ["QUICK_REPLY", "QUICK_REPLY"]
        assert [b["text"] for b in buttons["buttons"]] == expected


def test_buttons_follow_settings() -> None:
    without = _templates(visit_buttons=False, recall_buttons=False)
    assert all(_component(t, "BUTTONS") is None for t in without)


def test_months_example_follows_setting() -> None:
    recall = next(t for t in _templates(recall_months=12) if t.name == "recall_reminder")
    body = _component(recall, "BODY")
    assert body is not None and body["example"]["body_text"][0][1] == "12"


def test_script_creates_exactly_what_copier_copies() -> None:
    """Иначе клиники, подключившиеся сами, не получат шаблон, созданный скриптом."""
    assert {t.name for t in script.planned((RU, KY))} == set(copied_template_names())


def test_create_payload_has_no_status() -> None:
    payload = _templates()[0].create_payload()
    assert set(payload) == {"name", "language", "category", "components"}


# ── Скрипт ──────────────────────────────────────────────────────────────────


class _FakeOnboarding:
    """WABA Meta в памяти: что в ней лежит и что в неё создавали."""

    def __init__(self, existing: list[TemplateDefinition], refuse: str | None = None) -> None:
        self.existing = existing
        self.refuse = refuse
        self.created: list[tuple[str, str]] = []

    async def template_definitions(self, waba_id: str, token: str) -> list[TemplateDefinition]:
        assert (waba_id, token) == ("1234567890", "test-token")
        return self.existing

    async def create_template(self, waba_id: str, token: str, template: TemplateDefinition) -> str:
        if template.language == self.refuse:
            raise MetaOnboardingError("Invalid parameter", status=400, meta_code=100)
        self.created.append((template.name, template.language))
        return "PENDING"


@pytest.fixture
def _source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "wa_template_source_waba_id", "1234567890")
    monkeypatch.setattr(settings, "wa_cloud_access_token", "test-token")


def _approved(name: str, language: str) -> TemplateDefinition:
    return TemplateDefinition(
        name=name, language=language, category="UTILITY", status="APPROVED", components=()
    )


@pytest.mark.usefixtures("_source")
async def test_preview_sends_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    fake = _FakeOnboarding([_approved("visit_reminder", RU)])
    assert await script.run(apply=False, onboarding=fake) == 0
    assert fake.created == []
    out = capsys.readouterr().out
    assert "visit_reminder · ru · UTILITY: уже есть, APPROVED" in out
    assert "debt_reminder · ky_KG · UTILITY: будет создан" in out
    assert "Не хватает: 5" in out


@pytest.mark.usefixtures("_source")
async def test_apply_creates_only_missing() -> None:
    fake = _FakeOnboarding([_approved("visit_reminder", RU)])
    assert await script.run(apply=True, onboarding=fake) == 0
    assert ("visit_reminder", RU) not in fake.created
    assert len(fake.created) == 5


@pytest.mark.usefixtures("_source")
async def test_refused_language_does_not_stop_the_rest(capsys: pytest.CaptureFixture[str]) -> None:
    fake = _FakeOnboarding([], refuse=KY)
    assert await script.run(apply=True, onboarding=fake) == 1
    assert fake.created == [(name, RU) for name in _VALUES]
    assert "Meta отказала — Invalid parameter (код 100)" in capsys.readouterr().out


@pytest.mark.usefixtures("_source")
async def test_lang_limits_what_is_created() -> None:
    fake = _FakeOnboarding([])
    assert await script.run(apply=True, languages=(RU,), onboarding=fake) == 0
    assert {lang for _, lang in fake.created} == {RU}


async def test_missing_settings_fail_without_calling_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "wa_template_source_waba_id", "")
    fake = _FakeOnboarding([])
    assert await script.run(apply=True, onboarding=fake) == 1
    assert fake.created == []
