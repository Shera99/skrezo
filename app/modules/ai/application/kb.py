"""Редактор базы знаний (Ф6): создание, правка, версии, откат, сводка токенов.

Единственная точка записи в ``kb_document`` — сюда же приходит ответ на
пробел знаний. Каждое сохранение оставляет версию, поэтому знание вступает в
силу сразу (следующий ход бота), но к любой точке можно вернуться.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.ai.domain.knowledge import (
    KB_CATEGORIES,
    KB_LANGS,
    KB_TOKEN_LIMIT,
    estimate_tokens,
    fits_in_prompt,
    mentions_price,
)
from app.modules.ai.infrastructure.repositories.kb_repository import KbRepository
from app.shared.db.models.kb_document import KbDocument
from app.shared.db.models.kb_document_version import KbDocumentVersion
from app.shared.db.updates import apply_updates

_UPDATABLE = frozenset({"title", "category", "body", "is_active"})


@dataclass(frozen=True, slots=True)
class KbLangUsage:
    docs: int
    tokens_estimate: int


@dataclass(frozen=True, slots=True)
class KbUsage:
    """Сколько знания уходит в промпт по языкам и какие документы помещаются."""

    by_lang: dict[str, KbLangUsage]
    in_prompt: set[uuid.UUID]
    limit_tokens: int = KB_TOKEN_LIMIT


def validate_document(*, title: str, category: str, body: str, lang: str) -> None:
    """Правила сохранения: словарь категорий и языков, ни одной цены в тексте."""
    if category not in KB_CATEGORIES:
        raise ValidationAppError(
            f"Категория из словаря: {', '.join(sorted(KB_CATEGORIES))}",
            code="kb_category_unknown",
        )
    if lang not in KB_LANGS:
        raise ValidationAppError("Язык документа: ru или ky", code="kb_lang_unknown")
    if mentions_price(body) or mentions_price(title):
        raise ValidationAppError(
            "Цены в базу знаний не кладём: бот берёт их из прайса, а документ устареет",
            code="kb_price_forbidden",
        )


class KbService:
    def __init__(self, session: AsyncSession, repository: KbRepository) -> None:
        self._session = session
        self._repository = repository

    async def create(
        self,
        clinic_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
        title: str,
        category: str,
        body: str,
        lang: str,
        is_active: bool = True,
    ) -> KbDocument:
        """Новый документ + версия 1. Коммит — за вызывающим."""
        title, body = title.strip()[:200], body.strip()
        validate_document(title=title, category=category, body=body, lang=lang)
        document = KbDocument(
            clinic_id=clinic_id,
            title=title,
            category=category,
            body=body,
            lang=lang,
            is_active=is_active,
            version_no=1,
        )
        self._session.add(document)
        await self._session.flush()
        self._snapshot(document, action="created", actor_id=actor_id, note=None)
        return document

    async def update(
        self,
        clinic_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
        fields: Mapping[str, Any],
    ) -> KbDocument:
        """Частичная правка по списку колонок; каждое сохранение — новая версия."""
        document = await self._get(clinic_id, document_id)
        if not fields:
            return document
        merged = {
            "title": document.title,
            "category": document.category,
            "body": document.body,
            **{k: v for k, v in fields.items() if k in {"title", "category", "body"}},
        }
        validate_document(
            title=str(merged["title"]).strip(),
            category=str(merged["category"]),
            body=str(merged["body"]).strip(),
            lang=document.lang,
        )
        apply_updates(
            document,
            {k: (v.strip() if isinstance(v, str) else v) for k, v in fields.items()},
            updatable=_UPDATABLE,
            not_null=_UPDATABLE,
            entity_name="документа базы знаний",
        )
        document.version_no += 1
        self._snapshot(document, action="updated", actor_id=actor_id, note=None)
        await self._session.commit()
        return document

    async def rollback(
        self,
        clinic_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
        version_no: int,
    ) -> KbDocument:
        """Вернуть содержимое версии N — как новую версию, история не переписывается."""
        document = await self._get(clinic_id, document_id)
        version = await self._repository.version(document.id, version_no)
        if version is None:
            raise NotFoundError("Версии с таким номером нет", code="kb_version_not_found")
        document.title = version.title
        document.category = version.category
        document.body = version.body
        document.is_active = version.is_active
        document.version_no += 1
        self._snapshot(
            document, action="rolled_back", actor_id=actor_id, note=f"к версии {version_no}"
        )
        await self._session.commit()
        return document

    async def delete(self, clinic_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Безвозвратно (версии уходят каскадом); чтобы просто выключить — is_active."""
        document = await self._get(clinic_id, document_id)
        await self._session.delete(document)
        await self._session.commit()

    async def get_with_versions(
        self, clinic_id: uuid.UUID, document_id: uuid.UUID
    ) -> tuple[KbDocument, list[KbDocumentVersion]]:
        document = await self._get(clinic_id, document_id)
        return document, await self._repository.versions(document.id)

    async def usage(self, clinic_id: uuid.UUID) -> KbUsage:
        """Сводка редактора: токены по языкам и кто помещается в промпт.

        Правило то же, что у сборки промпта: по языку, в порядке создания, пока
        сумма длин не превысила потолок.
        """
        rows = await self._repository.active_lengths(clinic_id)
        by_lang: dict[str, KbLangUsage] = {}
        in_prompt: set[uuid.UUID] = set()
        for lang in sorted({lang for _, lang, _ in rows} | KB_LANGS):
            mine = [(doc_id, length) for doc_id, doc_lang, length in rows if doc_lang == lang]
            flags = fits_in_prompt(length for _, length in mine)
            in_prompt.update(doc_id for (doc_id, _), ok in zip(mine, flags, strict=True) if ok)
            by_lang[lang] = KbLangUsage(
                docs=len(mine),
                tokens_estimate=sum(estimate_tokens(" " * length) for _, length in mine),
            )
        return KbUsage(by_lang=by_lang, in_prompt=in_prompt)

    def _snapshot(
        self, document: KbDocument, *, action: str, actor_id: uuid.UUID | None, note: str | None
    ) -> None:
        self._session.add(
            KbDocumentVersion(
                clinic_id=document.clinic_id,
                document_id=document.id,
                version_no=document.version_no,
                title=document.title,
                category=document.category,
                body=document.body,
                lang=document.lang,
                is_active=document.is_active,
                action=action,
                note=note,
                actor_id=actor_id,
            )
        )

    async def _get(self, clinic_id: uuid.UUID, document_id: uuid.UUID) -> KbDocument:
        document = await self._repository.get(clinic_id, document_id)
        if document is None:
            raise NotFoundError("Документ базы знаний не найден", code="kb_document_not_found")
        return document
