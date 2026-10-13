"""Юнит-тесты голосового помощника без БД: когда он включён и как считается смета."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.modules.llm.infrastructure.gemini_client import build_llm_client
from app.modules.usage.domain.rates import RateCard, UsageTotals, estimate_cost_tiyin


@pytest.mark.parametrize(
    ("provider", "shared_key", "app_key", "enabled"),
    [
        ("off", "", "", False),
        # Выключатель ассистента общий: общий ключ при off помощника не включает.
        ("off", "shared", "", False),
        ("gemini", "shared", "", True),
        # Свой ключ включает помощника независимо от ассистента пациентов.
        ("off", "", "app", True),
        ("off", "shared", "app", True),
    ],
)
def test_build_llm_client_respects_provider_switch(
    monkeypatch: pytest.MonkeyPatch, provider: str, shared_key: str, app_key: str, enabled: bool
) -> None:
    monkeypatch.setattr(settings, "ai_provider", provider)
    monkeypatch.setattr(settings, "gemini_api_key", shared_key)
    monkeypatch.setattr(settings, "gemini_app_api_key", app_key)

    assert (build_llm_client() is not None) is enabled


def test_build_llm_client_prefers_own_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ai_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "shared")
    monkeypatch.setattr(settings, "gemini_app_api_key", "app")

    client = build_llm_client()

    assert client is not None
    assert client._api_key == "app"


def test_voice_tokens_are_priced_like_model_tokens() -> None:
    """Голосовые токены — по ставкам модели, без скидки кэша префикса."""
    rates = RateCard(
        ai_input_per_mtok=1_000_000, ai_output_per_mtok=2_000_000, ai_cached_input_per_mtok=0
    )
    totals = UsageTotals(
        model_input_tokens=1000,
        model_output_tokens=100,
        model_cached_tokens=400,
        voice_calls=3,
        voice_input_tokens=500,
        voice_output_tokens=50,
    )

    # (1000 − 400 кэш + 500 голос) × 1 + (100 + 50) × 2
    assert estimate_cost_tiyin(totals, rates) == 1100 + 300
    assert (totals + totals).voice_calls == 6
