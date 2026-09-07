"""Правила регрессии из выученных ответов: ключевые слова и порог узнавания."""

from __future__ import annotations

from app.modules.ai.domain.learning import extract_keywords, keyword_hits, keywords_pass


def test_keywords_skip_stopwords_and_merge_inflections() -> None:
    words = extract_keywords(
        "Здравствуйте! У клиники есть бесплатная парковка во дворе, парковки хватает всем."
    )
    assert words == ["бесплатная", "парковка", "дворе", "хватает"]


def test_hits_are_matched_by_stem_and_half_is_enough() -> None:
    keywords = ["бесплатная", "парковка", "токтогула", "въезд"]
    hits = keyword_hits("Парковку у нас бесплатную найдёте во дворе.", keywords)
    assert hits == ["бесплатная", "парковка"]
    assert keywords_pass(len(hits), len(keywords)) is True
    assert keywords_pass(1, 4) is False
    assert keywords_pass(0, 0) is True
