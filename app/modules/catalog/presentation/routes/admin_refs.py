"""Роуты: GET /admin/refs/cities|specializations — справочники для админ-панели.

Те же данные, что публичные `/cities` и `/specializations`, но под платформенным
токеном (используются формами и фильтрами админки).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.modules.catalog.application.use_cases.list_cities import ListCitiesUseCase
from app.modules.catalog.application.use_cases.list_specializations import (
    ListSpecializationsUseCase,
)
from app.modules.catalog.presentation.deps import (
    get_list_cities_use_case,
    get_list_specializations_use_case,
)
from app.modules.catalog.presentation.schemas import CatalogItemOut
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()


@router.get(
    "/admin/refs/cities",
    response_model=list[CatalogItemOut],
    summary="Города (админ-справочник)",
    response_description="Активные города на языке запроса",
)
async def admin_cities(
    lang: Lang = Depends(get_request_lang),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListCitiesUseCase = Depends(get_list_cities_use_case),
) -> list[CatalogItemOut]:
    return await use_case.execute(lang)  # type: ignore[return-value]


@router.get(
    "/admin/refs/specializations",
    response_model=list[CatalogItemOut],
    summary="Специализации (админ-справочник)",
    response_description="Активные специализации на языке запроса",
)
async def admin_specializations(
    lang: Lang = Depends(get_request_lang),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListSpecializationsUseCase = Depends(get_list_specializations_use_case),
) -> list[CatalogItemOut]:
    return await use_case.execute(lang)  # type: ignore[return-value]
