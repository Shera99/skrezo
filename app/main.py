"""Точка входа FastAPI-приложения платформенной зоны Skrezo.

Собирает приложение: метаданные OpenAPI (на русском), CORS, обработчики
ошибок, роутеры модулей и служебный ``/health``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.core.config import settings
from app.core.db import engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.redis import redis_client
from app.modules.appointments.presentation.routes import router as appointments_router
from app.modules.audit.presentation.routes import router as audit_router
from app.modules.catalog.presentation.routes import router as catalog_router
from app.modules.clients.presentation.routes import router as clients_router
from app.modules.dashboard.presentation.routes import router as dashboard_router
from app.modules.identity.presentation.routes import router as identity_router
from app.modules.leads.presentation.routes import router as leads_router
from app.modules.patients.presentation.routes import router as patients_router
from app.modules.payments.presentation.routes import router as payments_router
from app.modules.schedule.presentation.routes import router as schedule_router
from app.modules.services.presentation.routes import router as services_router
from app.modules.staff.presentation.routes import router as staff_router
from app.modules.tariffs.presentation.routes import router as tariffs_router

OPENAPI_DESCRIPTION = """
Бэкенд **платформенной зоны Skrezo** (админ-панель команды) и **рабочего
приложения клиник**.

**Единый вход** по телефону + паролю для всех (`POST /auth/login`), доступ
разграничивают роли:

* **Команда Skrezo** — аккаунт с признаком `platform_admin` → эндпоинты `/admin/*`.
* **Сотрудники клиник** (владелец, менеджер, врач) — доступ по членству в клинике
  и правам (RBAC) → эндпоинты `/clinic/*`.

Вход пациентов по OTP — в их приложении (glo), вне этого сервиса. Все суммы —
целые сомы, все идентификаторы — UUID v4.
""".strip()

# ``x-displayName`` — расширение ReDoc: русское имя раздела в левом меню
# (сам ``name`` остаётся слагом-идентификатором, на который ссылаются роуты).
OPENAPI_TAGS = [
    {
        "name": "health",
        "x-displayName": "Служебное",
        "description": "Проверка живости сервиса.",
    },
    {
        "name": "catalog",
        "x-displayName": "Справочники",
        "description": "Справочники: города и специализации (+ /admin/refs).",
    },
    {
        "name": "auth",
        "x-displayName": "Вход (единый)",
        "description": "Единый вход по телефону + паролю для всех: команда Skrezo "
        "(claim platform_admin) и сотрудники клиник.",
    },
    {
        "name": "leads",
        "x-displayName": "Лиды",
        "description": "Лид-форма «Больше одного» (публичная).",
    },
    {
        "name": "tariffs",
        "x-displayName": "Тарифы",
        "description": "Тарифы Skrezo: версии, пакеты клиники + «Сеть», история.",
    },
    {
        "name": "clients",
        "x-displayName": "Клиенты",
        "description": "Клиенты: список, карточка, создание, врачи, смена номера.",
    },
    {
        "name": "staff",
        "x-displayName": "Сотрудники клиники",
        "description": "Сотрудники клиники (владелец/менеджер, RBAC).",
    },
    {
        "name": "schedule",
        "x-displayName": "Расписание",
        "description": "Расписание клиники: лента дня, свободные слоты, часы работы "
        "и графики врачей.",
    },
    {
        "name": "patients",
        "x-displayName": "Пациенты",
        "description": "Клиническое ядро: глобальные пациенты, одонтограмма (FDI), история зуба.",
    },
    {
        "name": "services",
        "x-displayName": "Услуги (прайс)",
        "description": "Прайс клиники: услуги, цены, длительность.",
    },
    {
        "name": "appointments",
        "x-displayName": "Приёмы",
        "description": "Приёмы (клиника+врач+пациент) и история лечения по зубам.",
    },
    {
        "name": "payments",
        "x-displayName": "Платежи",
        "description": "Платежи Skrezo: журнал и сводка (v1 — заглушка).",
    },
    {
        "name": "dashboard",
        "x-displayName": "Дашборд",
        "description": "Дашборд: метрики за период (active/trial/риски).",
    },
    {
        "name": "audit",
        "x-displayName": "Аудит",
        "description": "Журнал аудита мутаций платформенной зоны.",
    },
]

# ``x-tagGroups`` — расширение ReDoc: верхнеуровневые группы разделов в меню.
# Пациентского API нет (клиническое ядро — приложение glo, вне скоупа); когда
# появится — завести группу «Пациенты».
OPENAPI_TAG_GROUPS = [
    {
        "name": "Вход",
        "tags": ["auth"],
    },
    {
        "name": "Админ-панель Skrezo",
        "tags": ["clients", "tariffs", "payments", "dashboard", "audit"],
    },
    {
        "name": "Рабочее приложение клиники",
        "tags": ["staff", "schedule", "patients", "services", "appointments"],
    },
    {
        "name": "Публичное и служебное",
        "tags": ["catalog", "leads", "health"],
    },
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Жизненный цикл: настройка логов на старте, закрытие ресурсов на стопе."""
    configure_logging()
    yield
    await redis_client.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    """Фабрика приложения."""
    app = FastAPI(
        title="Skrezo Platform API",
        version="0.1.0",
        description=OPENAPI_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        contact={"name": "Skrezo", "email": "dev@skrezo.local"},
        license_info={"name": "Proprietary"},
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)

    @app.get("/health", tags=["health"], summary="Проверка живости")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Роутеры модулей под /api/v1
    app.include_router(catalog_router, prefix=settings.api_v1_prefix)
    app.include_router(identity_router, prefix=settings.api_v1_prefix)
    app.include_router(leads_router, prefix=settings.api_v1_prefix)
    app.include_router(tariffs_router, prefix=settings.api_v1_prefix)
    app.include_router(clients_router, prefix=settings.api_v1_prefix)
    app.include_router(staff_router, prefix=settings.api_v1_prefix)
    app.include_router(schedule_router, prefix=settings.api_v1_prefix)
    app.include_router(patients_router, prefix=settings.api_v1_prefix)
    app.include_router(services_router, prefix=settings.api_v1_prefix)
    app.include_router(appointments_router, prefix=settings.api_v1_prefix)
    app.include_router(audit_router, prefix=settings.api_v1_prefix)
    app.include_router(payments_router, prefix=settings.api_v1_prefix)
    app.include_router(dashboard_router, prefix=settings.api_v1_prefix)

    def custom_openapi() -> dict[str, Any]:
        """Схема OpenAPI с группами разделов ReDoc (``x-tagGroups``)."""
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=OPENAPI_TAGS,
            contact=app.contact,
            license_info=app.license_info,
        )
        schema["x-tagGroups"] = OPENAPI_TAG_GROUPS
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app


app = create_app()
