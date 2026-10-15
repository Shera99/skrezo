"""Раннер регрессионного набора (§13.3) — общий для CI и ручного прогона.

Кейсы лежат в ``regression/ai_cases.yaml`` рядом с кодом: правка промпта и
правка ожиданий видны в одном коммите. Прогон обязателен перед изменением
промпта, базы знаний, глоссария или модели — иначе любая правка становится
стрельбой вслепую: поправили одно, сломали другое, узнали от пациента.

Здесь только детерминированный уровень: медицинский фильтр и валидатор — наш
код, модели он не требует и потому идёт в CI на каждый коммит.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any

import yaml

from app.modules.ai.domain.facts import TurnFacts
from app.modules.ai.domain.medical import classify
from app.modules.ai.domain.prompt import ClinicCard, build_system_prompt
from app.modules.ai.domain.reply import BotReply
from app.modules.ai.domain.validator import validate

#: Набор живёт в корне репозитория — он продуктовый артефакт, а не тест-фикстура.
CASES_PATH = pathlib.Path(__file__).resolve().parents[4] / "regression" / "ai_cases.yaml"

#: Справочник врачей для кейсов валидатора: намеренно короткий и фиксированный.
_CASE_DOCTORS = ["Асанова Айгуль", "Иванов Пётр"]


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Итог одного кейса."""

    case_id: str
    ok: bool
    expected: str
    actual: str
    note: str

    def __str__(self) -> str:
        mark = "ok " if self.ok else "FAIL"
        return (
            f"{mark} {self.case_id:10} ждали {self.expected:18} "
            f"получили {self.actual:18} {self.note}"
        )


def load_cases(path: pathlib.Path | None = None) -> list[dict[str, Any]]:
    doc = yaml.safe_load((path or CASES_PATH).read_text())
    cases: list[dict[str, Any]] = doc["cases"]
    return cases


def run_case(case: dict[str, Any]) -> CaseResult:
    """Прогнать один кейс детерминированного уровня."""
    kind = case["kind"]
    note = str(case.get("note") or "")
    if kind == "medical":
        expected = str(case["expect"]["medical"])
        actual = classify(str(case["message"])).kind.value
        return CaseResult(case["id"], actual == expected, expected, actual, note)
    if kind == "validator":
        return _run_validator_case(case, note)
    if kind == "prompt":
        return _run_prompt_case(case, note)
    return CaseResult(case["id"], False, "known kind", f"неизвестный вид {kind}", note)


def _run_validator_case(case: dict[str, Any], note: str) -> CaseResult:
    expect = case["expect"]
    expected = str(expect["action"])
    if expect.get("code"):
        expected = f"{expected}/{expect['code']}"
    if expect.get("signal"):
        expected = f"{expected}+{expect['signal']}"

    reply = BotReply(
        reply=str(case["reply"]),
        lang=str(case["lang"]),
        intent="info",
        used_facts=list(case.get("usedFacts") or []),
        confidence=str(case.get("confidence") or "high"),
        needs_human=bool(case.get("needsHuman")),
        human_reason=None,
    )
    facts = TurnFacts.build(
        list(case.get("facts") or []),
        list(case.get("doctors") or _CASE_DOCTORS),
        list(case.get("foreignClinics") or []),
        own_clinic=case.get("clinicName"),
    )
    verdict = validate(reply, case.get("conversationLang"), facts)
    actual = verdict.action.value
    if verdict.codes:
        actual = f"{actual}/{verdict.codes[0]}"
    if verdict.signals:
        # Сигнал без блока (V09) — тоже часть ожидания: пропавший сигнал — регресс.
        actual = f"{actual}+{','.join(sorted(verdict.signals))}"
    return CaseResult(case["id"], actual == expected, expected, actual, note)


def _run_prompt_case(case: dict[str, Any], note: str) -> CaseResult:
    """Правило не должно исчезнуть из промпта незамеченным.

    Самая дешёвая и самая частая поломка при правке промпта — случайно
    удалённый блок: модель начинает вести себя иначе, а тесты молчат.
    """
    needle = str(case["contains"])
    card = ClinicCard(name="Проверка", address=None, phone=None, working_hours=None)
    prompt = build_system_prompt(card, "ru", [], [])
    found = needle in prompt
    return CaseResult(
        case["id"], found, f"есть «{needle[:16]}…»", "есть" if found else "ПОТЕРЯНО", note
    )


def run_all(path: pathlib.Path | None = None) -> list[CaseResult]:
    """Весь детерминированный набор; кейсы уровня «живая модель» пропускаются."""
    kinds = {"medical", "validator", "prompt"}
    return [run_case(c) for c in load_cases(path) if c["kind"] in kinds]
