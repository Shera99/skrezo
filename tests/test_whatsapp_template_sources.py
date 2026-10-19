"""Юнит-тесты источников значений переменных шаблона WhatsApp (без БД)."""

from __future__ import annotations

from app.modules.whatsapp.domain.template_sources import (
    VariableSource,
    normalize_sources,
    parse_source,
)
from app.modules.whatsapp.domain.templates import variable_labels
from app.shared.db.enums import Lang


def test_sources_follow_variables_count() -> None:
    """Новая версия шаблона с другим числом переменных: лишнее — прочь, недостающее — вручную."""
    assert normalize_sources(["1", "2", "3"], ["patient_name"]) == [
        VariableSource.PATIENT_NAME,
        VariableSource.MANUAL,
        VariableSource.MANUAL,
    ]
    assert normalize_sources(["1"], ["clinic_name", "debt_amount"]) == [VariableSource.CLINIC_NAME]


def test_unknown_source_falls_back_to_manual() -> None:
    assert parse_source("salary") is VariableSource.MANUAL


def test_labels_prefer_own_then_source_then_placeholder() -> None:
    labels = variable_labels(
        ["1", "2", "3"], ["Как обращаться", "", ""], ["patient_name", "clinic_name", "manual"]
    )
    assert labels == ["Как обращаться", "Название клиники", "{{3}}"]


def test_source_label_is_localized_with_russian_fallback() -> None:
    assert variable_labels(["1"], [], ["patient_name"], Lang.KG) == ["Пациенттин аты"]
    assert variable_labels(["1"], [], ["patient_name"], Lang.EN) == ["Имя пациента"]
