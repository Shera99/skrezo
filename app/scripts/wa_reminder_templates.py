"""Шаблоны напоминаний Skrezo в WABA платформы: показать и создать недостающие.

Без ``--apply`` ничего не отправляет: печатает, какие шаблоны напоминаний
(визит, долг, повторный осмотр) в WABA уже есть, и тела запросов для
недостающих. С ``--apply`` создаёт недостающие пары (имя, язык) через Graph API.
Meta проверяет каждый шаблон сама: статус сначала ``PENDING``. Повторный запуск
безопасен — существующий шаблон не трогается, даже отклонённый: его правят в
WhatsApp Manager.

Состав шаблонов — :mod:`app.modules.whatsapp.domain.reminder_templates`.
После одобрения клиникам, подключившим номер сами, копии создаст ежечасный
монитор номеров; остальным шаблоны попадут в каталог при синхронизации.

    uv run python -m app.scripts.wa_reminder_templates
    uv run python -m app.scripts.wa_reminder_templates --apply
    uv run python -m app.scripts.wa_reminder_templates --apply --lang ru

На сервере (без make):

    sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && \\
        .venv/bin/python -m app.scripts.wa_reminder_templates'

WABA — ``WA_TEMPLATE_SOURCE_WABA_ID`` (или ``--waba``), токен —
``WA_CLOUD_ACCESS_TOKEN``. Код выхода 0 — всё на месте, создано или показано;
1 — нет настроек, Meta не ответила или отказала хотя бы по одному шаблону.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Protocol

import httpx

from app.core.config import settings
from app.modules.whatsapp.domain.reminder_templates import KY, RU, reminder_templates
from app.shared.whatsapp_cloud import MetaOnboardingError, TemplateDefinition
from app.shared.whatsapp_cloud.factory import GRAPH_TIMEOUT
from app.shared.whatsapp_cloud.onboarding import GraphOnboardingClient

#: Как язык называется в ``--lang`` → код Meta.
LANGUAGES = {"ru": RU, "ky": KY}


class TemplateRegistry(Protocol):
    """Шаблоны WABA в Meta — всё, что нужно скрипту от клиента Graph."""

    async def template_definitions(self, waba_id: str, token: str) -> list[TemplateDefinition]: ...

    async def create_template(
        self, waba_id: str, token: str, template: TemplateDefinition
    ) -> str: ...


def planned(languages: tuple[str, ...]) -> list[TemplateDefinition]:
    """Шаблоны, которые должны быть в WABA, — с именами и кнопками из настроек."""
    return reminder_templates(
        visit_name=settings.wa_visit_reminder_template,
        debt_name=settings.wa_debt_reminder_template,
        recall_name=settings.wa_recall_reminder_template,
        visit_buttons=settings.wa_visit_reminder_buttons,
        recall_buttons=settings.wa_recall_buttons,
        recall_months=settings.recall_months,
        languages=languages,
    )


def _payload(template: TemplateDefinition) -> str:
    text = json.dumps(template.create_payload(), ensure_ascii=False, indent=2)
    return "\n".join(f"     {line}" for line in text.splitlines())


async def run(
    *,
    apply: bool,
    languages: tuple[str, ...] = (RU, KY),
    waba_id: str | None = None,
    onboarding: TemplateRegistry | None = None,
) -> int:
    """Сверить WABA с нужными шаблонами; при ``apply`` — создать недостающие."""
    waba = (waba_id or settings.wa_template_source_waba_id).strip()
    token = settings.wa_cloud_access_token.strip()
    if not waba:
        print("❌ Не задан WA_TEMPLATE_SOURCE_WABA_ID (или --waba): ID WABA Skrezo.")
        return 1
    if not token:
        print("❌ Не задан WA_CLOUD_ACCESS_TOKEN: токен с доступом к WABA Skrezo.")
        return 1
    if onboarding is not None:
        return await _reconcile(onboarding, waba, token, planned(languages), apply=apply)
    async with httpx.AsyncClient(timeout=GRAPH_TIMEOUT) as client:
        # Секрет приложения для работы с шаблонами не нужен — только токен.
        graph = GraphOnboardingClient(
            settings.wa_cloud_api_url,
            settings.wa_cloud_app_id,
            settings.wa_cloud_app_secret,
            client,
        )
        return await _reconcile(graph, waba, token, planned(languages), apply=apply)


async def _reconcile(
    onboarding: TemplateRegistry,
    waba: str,
    token: str,
    templates: list[TemplateDefinition],
    *,
    apply: bool,
) -> int:
    try:
        existing = await onboarding.template_definitions(waba, token)
    except MetaOnboardingError as exc:
        print(
            f"❌ WABA {waba} не отвечает: {exc} (код Meta {exc.meta_code}) — проверьте ID и токен."
        )
        return 1
    have = {(t.name, t.language): t.status for t in existing}
    print(f"WABA {waba}: шаблонов всего {len(existing)}.")
    print(
        "Режим: создание."
        if apply
        else "Режим: просмотр, в Meta ничего не уходит (--apply — создать)."
    )
    print()

    missing = created = failed = 0
    for template in templates:
        label = f"{template.name} · {template.language} · {template.category}"
        status = have.get((template.name, template.language))
        if status is not None:
            hint = " — правьте в WhatsApp Manager" if status == "REJECTED" else ""
            print(f"✅ {label}: уже есть, {status}{hint}")
            continue
        missing += 1
        if not apply:
            print(f"➕ {label}: будет создан")
            print(_payload(template))
            continue
        try:
            status = await onboarding.create_template(waba, token, template)
        except MetaOnboardingError as exc:
            failed += 1
            print(f"❌ {label}: Meta отказала — {exc} (код {exc.meta_code})")
            continue
        created += 1
        print(f"🆕 {label}: создан, статус {status}")

    print()
    if not missing:
        print("Все шаблоны напоминаний на месте.")
    elif not apply:
        print(f"Не хватает: {missing}. Создать: тот же запуск с --apply.")
    else:
        print(f"Создано: {created} из {missing}. Решение Meta придёт в статусе шаблона.")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Шаблоны напоминаний Skrezo в WABA платформы")
    parser.add_argument("--apply", action="store_true", help="создать недостающие шаблоны в Meta")
    parser.add_argument(
        "--lang",
        action="append",
        choices=sorted(LANGUAGES),
        help="только этот язык (можно повторить); по умолчанию ru и ky",
    )
    parser.add_argument("--waba", help="ID WABA вместо WA_TEMPLATE_SOURCE_WABA_ID")
    args = parser.parse_args()
    languages = tuple(LANGUAGES[code] for code in args.lang) if args.lang else (RU, KY)
    sys.exit(asyncio.run(run(apply=args.apply, languages=languages, waba_id=args.waba)))


if __name__ == "__main__":
    main()
