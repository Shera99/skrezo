"""Регрессионный набор ассистента в CI (§13.3).

Детерминированный уровень — медицинский фильтр, валидатор и целостность
промпта — модели не требует, поэтому идёт на каждом коммите. Правка промпта
без обновления набора обязана падать здесь, а не у пациента.
"""

from __future__ import annotations

from app.modules.ai.domain.regression import load_cases, run_all


def test_regression_suite_is_green() -> None:
    results = run_all()
    failed = [str(r) for r in results if not r.ok]
    assert not failed, "провалены кейсы:\n" + "\n".join(failed)


def test_suite_covers_both_languages_and_required_areas() -> None:
    """§13.3: набор двуязычный, с отказами и медфильтром — иначе он декоративный."""
    cases = load_cases()
    by_lang = {"ru": 0, "ky": 0}
    for case in cases:
        by_lang[str(case["lang"])] = by_lang.get(str(case["lang"]), 0) + 1
    assert by_lang["ru"] >= 25, by_lang
    assert by_lang["ky"] >= 25, by_lang

    medical = [c for c in cases if c["kind"] == "medical"]
    assert len(medical) >= 5

    refusals = [
        c
        for c in cases
        if c["kind"] == "validator" and c["expect"]["action"] in {"block", "escalate"}
    ]
    assert len(refusals) >= 10, f"кейсов на правильный отказ мало: {len(refusals)}"
