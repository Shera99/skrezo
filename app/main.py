"""Точка входа FastAPI-приложения платформенной зоны Skrezo.

Собирает приложение: метаданные OpenAPI (на русском), CORS, обработчики
ошибок, роутеры модулей и служебный ``/health``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.core.config import settings
from app.core.db import engine
from app.core.errors import register_exception_handlers
from app.core.jobs import close_job_queue, connect_job_queue
from app.core.logging import configure_logging
from app.core.redis import redis_client
from app.modules.access.presentation.routes import admin_router as access_admin_router
from app.modules.access.presentation.routes import router as roles_router
from app.modules.accounts.presentation.routes import router as accounts_router
from app.modules.ai.presentation.routes import router as ai_router
from app.modules.appointments.presentation.routes import router as appointments_router
from app.modules.audit.presentation.routes import clinic_router as clinic_audit_router
from app.modules.audit.presentation.routes import router as audit_router
from app.modules.cash.presentation.routes import router as cash_router
from app.modules.catalog.presentation.routes import router as catalog_router
from app.modules.clients.presentation.routes import router as clients_router
from app.modules.clinic_applications.presentation.routes import (
    admin_router as clinic_applications_admin_router,
)
from app.modules.clinic_applications.presentation.routes import (
    router as clinic_applications_router,
)
from app.modules.clinics.presentation.routes import router as clinic_settings_router
from app.modules.dashboard.presentation.routes import router as dashboard_router
from app.modules.identity.presentation.routes import onboarding_router
from app.modules.identity.presentation.routes import router as identity_router
from app.modules.jobs.presentation.routes import router as jobs_router
from app.modules.leads.presentation.routes import router as leads_router
from app.modules.patients.presentation.routes import router as patients_router
from app.modules.payments.presentation.routes import router as payments_router
from app.modules.push.presentation.routes import router as push_router
from app.modules.reminders.presentation.routes import router as reminders_router
from app.modules.reports.presentation.routes import admin_router as reports_admin_router
from app.modules.reports.presentation.routes import router as reports_router
from app.modules.schedule.presentation.routes import router as schedule_router
from app.modules.services.presentation.routes import router as services_router
from app.modules.staff.presentation.routes import router as staff_router
from app.modules.subscriptions.presentation.routes import (
    admin_router as subscription_admin_router,
)
from app.modules.subscriptions.presentation.routes import router as subscription_router
from app.modules.tariffs.presentation.routes import router as tariffs_router
from app.modules.usage.presentation.routes import router as usage_router
from app.modules.whatsapp.presentation.routes import admin_router as whatsapp_admin_router
from app.modules.whatsapp.presentation.routes import router as whatsapp_router
from app.realtime import build_asgi, sio
from app.realtime.listener import run_realtime_listener
from app.shared.http import close_shared_clients

OPENAPI_DESCRIPTION = """
Бэкенд **платформенной зоны Skrezo** (админ-панель команды) и **рабочего
приложения клиник**.

**Два способа входа, один контур токенов.** Способ определяет признак
`platform_admin`, а не то, какую ручку дёрнул клиент:

* **Сотрудники клиник и соло-врачи** (владелец, менеджер, врач) — вход по коду
  на телефон: `POST /auth/otp/request` → `POST /auth/otp/verify`. Доступ по
  членству в клинике и правам (RBAC) → эндпоинты `/clinic/*`.
* **Команда Skrezo** — телефон + пароль (`POST /auth/login`), аккаунт с признаком
  `platform_admin` → эндпоинты `/admin/*`.

Разделение строгое в обе стороны: код из SMS не логинит админ-панель
(`409 use_password_login`), пароль не логинит клинику (`401 invalid_credentials`).
Перехваченное сообщение не должно открывать доступ ко всем клиникам платформы.

Вход пациентов по OTP — в их приложении (glo), вне этого сервиса. Все суммы —
целые **тыйыны** (1/100 сома), поля с суффиксом `Tiyin`; идентификаторы — UUID v4.

**Контур ассистента (Ф1–Ф8).** Переписка с пациентами идёт через WhatsApp Cloud
API (раздел *WhatsApp*), отвечает AI-ассистент (раздел *AI-ассистент*): запись,
перенос и отмена — только по кнопке подтверждения и штатными use-case'ами;
цены, время и врачи — только из базы через инструменты; валидатор фактов до
отправки; медицинские жалобы — к врачу, не в обсуждение. Клиника управляет
доверием ступенями *Наблюдаю → Проверяю → Доверяю* и потолками умений, учит
бота своими данными (пробелы знаний, база знаний с версиями, регрессия,
метрики) и видит расход токенов и платных сообщений (раздел *Учёт*).
Напоминания пациентам о визите, долге и повторном осмотре — шаблонами
WhatsApp, включаются в кабинете. Оплата подписки — через платёжную страницу
FreedomPay (раздел *Платежи*), период открывает только подписанный серверный
колбэк.
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
        "description": "Вход по коду на телефон — клиники и соло-врачи; вход по "
        "паролю — только команда Skrezo (claim platform_admin).",
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
        "description": "Клиенты: список, карточка, создание, сотрудники, смена номера.",
    },
    {
        "name": "accounts",
        "x-displayName": "Аккаунты (Skrezo)",
        "description": (
            "Все люди платформы одним списком — команда Skrezo и сотрудники клиник. "
            "Аккаунт глобальный: исключают не его, а членство в клинике."
        ),
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
        "name": "cash",
        "x-displayName": "Касса клиники",
        "description": "Касса клиники: платежи и возвраты по приёмам, долги, сводка.",
    },
    {
        "name": "reminders",
        "x-displayName": "Напоминания",
        "description": (
            "Отметки «напомнили»: журнал связи сотрудника с пациентом по поводу — "
            "повторный осмотр, долг, визит. Хранятся на сервере и видны всем "
            "администраторам клиники (право `reminders.manage`); последняя отметка "
            "пациента — `lastReminderAt` в списке пациентов."
        ),
    },
    {
        "name": "reports",
        "x-displayName": "Отчёты клиники",
        "description": (
            "Операционные отчёты клиники: выручка, врачи, услуги, пациенты. "
            "Право `reports.view_all` — вся клиника, `reports.view_own` — только свои приёмы."
        ),
    },
    {
        "name": "reports-admin",
        "x-displayName": "Отчёты по клиникам",
        "description": (
            "Отчёты Skrezo: свод по всем клиникам (выбор клиники) и полный набор "
            "отчётов по выбранной клинике без ограничений по врачу."
        ),
    },
    {
        "name": "clinic-settings",
        "x-displayName": "Настройки клиники",
        "description": (
            "Клиника ведёт себя сама: название, город, контакты, язык и таймзона "
            "(права `settings.view` / `settings.edit`). Тариф — за Skrezo."
        ),
    },
    {
        "name": "subscription",
        "x-displayName": "Подписка клиники",
        "description": (
            "Подписка своей клиники: статус, срок триала или оплаченного периода "
            "и остаток дней (право `billing.view`). **Оплата онлайн**: цена по "
            "тарифу за N месяцев (`/clinic/subscription/price`), платёж и переход "
            "на страницу оплаты FreedomPay (`POST /clinic/subscription/payments`, "
            "право `billing.manage`), список и состояние платежей — период "
            "открывает только серверный колбэк провайдера."
        ),
    },
    {
        "name": "push",
        "x-displayName": "Уведомления",
        "description": (
            "Устройства сотрудника и его настройки уведомлений (тихие часы, "
            "отключённые типы) — свои, без отдельного права. Доставка: события "
            "→ инбокс → живые пуши FCM/APNs (рубильник `PUSH_ENABLED`), сводки "
            "дня и серверный план локальных уведомлений."
        ),
    },
    {
        "name": "ai",
        "x-displayName": "AI-ассистент",
        "description": (
            "Ассистент в WhatsApp: запись, перенос и отмена по кнопке "
            "подтверждения, глоссарий с очередью предложений, симулятор входящих "
            "для Skrezo. **Кабинет**: черновики режима «Проверяю» (отправить как "
            "есть или с правкой, отклонить), передача диалога человеку и обратно. "
            "**Обучение** (`/clinic/knowledge-gaps`, `/clinic/kb`, "
            "`/clinic/regression`, `/clinic/assistant/metrics`): пробелы знаний с "
            "ответом своими словами, база знаний с версиями и откатом, регрессия "
            "из выученных ответов, метрики и гейт доверия. **Доверие** "
            "(`/clinic/assistant/trust`): ступени Наблюдаю → Проверяю → Доверяю "
            "(вверх — только через гейт), выключатель с памятью ступени, матрица "
            "умений — запись, перенос/отмена, напоминания о визите, долге и "
            "повторном осмотре. Модель за интерфейсом AiProvider (Gemini, кэш "
            "префикса промпта; смена — env). Цены, время и врачи — только из БД "
            "через инструменты; валидатор до отправки; «не знаю» — правильный ответ."
        ),
    },
    {
        "name": "whatsapp",
        "x-displayName": "WhatsApp (переписка)",
        "description": (
            "Канал WhatsApp Cloud API: вебхук Meta, диалоги пациентов с фильтрами "
            "«Ждут ответа» / «Нужен человек», счётчиками и отметкой прочитанного, "
            "поиск по переписке, ответы клиники (право `conversations.manage`), "
            "медиа и расшифровки голосовых, привязка номера из админки. Свободный "
            "текст — только в 24-часовое окно после сообщения пациента; дальше — "
            "одобренные шаблоны (те же, что у напоминаний). Не путать с "
            "OTP-каналами доставки кодов."
        ),
    },
    {
        "name": "whatsapp-admin",
        "x-displayName": "WhatsApp (Skrezo)",
        "description": (
            "Общие шаблоны WhatsApp платформы: каталог и понятные названия с подписями "
            "переменных для кабинетов клиник (только `is_platform_admin`)."
        ),
    },
    {
        "name": "usage",
        "description": (
            "Учёт использования по клиникам (админка Skrezo): токены модели, "
            "диалоги с ассистентом, платные сообщения WhatsApp и смета по прайс-листу."
        ),
    },
    {
        "name": "payments",
        "x-displayName": "Платежи",
        "description": (
            "Платежи клиник за подписку (FreedomPay) глазами Skrezo: журнал со "
            "сводкой за период (успешно, отказы, возвраты, нетто) и отметка "
            "возвратов — деньги возвращаются в кабинете провайдера. Оплата со "
            "стороны клиники — в разделе «Подписка клиники», колбэк провайдера — "
            "в «Публичное и служебное»."
        ),
    },
    {
        "name": "payments-webhook",
        "x-displayName": "Оплата: колбэк провайдера",
        "description": (
            "Публичный `result_url` платёжной страницы FreedomPay: провайдер "
            "присылает результат платежа с подписью `pg_sig`, ответ — XML "
            "(`ok` / `rejected` / `error`). Только этот вызов открывает оплаченный "
            "период; повтор — идемпотентен. Автосписание — когда провайдер откроет "
            "рекуррентные платежи."
        ),
    },
    {
        "name": "dashboard",
        "x-displayName": "Дашборд",
        "description": "Дашборд: метрики за период (active/trial/риски).",
    },
    {
        "name": "roles",
        "x-displayName": "Роли и права",
        "description": (
            "Настройка ролей клиники: свои роли, наборы прав, каталог прав (право `roles.manage`)."
        ),
    },
    {
        "name": "trial-features",
        "x-displayName": "Триал-функции",
        "description": "Настройка набора прав, доступных клиникам на 30-дневном триале.",
    },
    {
        "name": "clinic-applications",
        "x-displayName": "Заявки клиник",
        "description": (
            "Второй путь регистрации: заявка «клиника с сотрудниками» после "
            "подтверждения телефона по OTP."
        ),
    },
    {
        "name": "clinic-applications-admin",
        "x-displayName": "Заявки клиник (Skrezo)",
        "description": "Рассмотрение заявок: список, карточка, статусы.",
    },
    {
        "name": "audit",
        "x-displayName": "Аудит",
        "description": "Журнал аудита мутаций платформенной зоны.",
    },
    {
        "name": "subscription-admin",
        "x-displayName": "Подписки клиентов (Skrezo)",
        "description": (
            "Ручное управление подпиской: отметить оплату (доступ до даты) и "
            "заблокировать. Платёжного провайдера пока нет — оплату отмечает человек."
        ),
    },
    {
        "name": "clinic-audit",
        "x-displayName": "Журнал действий клиники",
        "description": (
            "Кто из сотрудников что сделал: отмены приёмов, скидки, платежи и "
            "возвраты, правки пациентов и прайса (право `audit.view`)."
        ),
    },
    {
        "name": "jobs",
        "x-displayName": "Фоновые задачи",
        "description": (
            "Состояние воркера и журнал запусков фоновых задач: закрытие триалов, "
            "предупреждения о конце пробного периода, доставка сообщений, уборка журнала."
        ),
    },
]

# ``x-tagGroups`` — расширение ReDoc: верхнеуровневые группы разделов в меню.
# ⚠️ Тег, не попавший ни в одну группу, ReDoc в меню НЕ показывает — так
# «пропали» разделы ассистента и WhatsApp. Тест test_openapi_tag_groups
# требует, чтобы каждый тег состоял в группе.
# Пациентского API нет (клиническое ядро — приложение glo, вне скоупа); когда
# появится — завести группу «Пациенты».
OPENAPI_TAG_GROUPS = [
    {
        "name": "Вход",
        "tags": ["auth"],
    },
    {
        "name": "Админ-панель Skrezo",
        "tags": [
            "clients",
            "accounts",
            "clinic-applications-admin",
            "tariffs",
            "payments",
            "usage",
            "dashboard",
            "reports-admin",
            "trial-features",
            "audit",
            "subscription-admin",
            "jobs",
        ],
    },
    {
        "name": "WhatsApp и AI-ассистент",
        "tags": ["whatsapp", "whatsapp-admin", "ai"],
    },
    {
        "name": "Рабочее приложение клиники",
        "tags": [
            "staff",
            "roles",
            "schedule",
            "patients",
            "services",
            "appointments",
            "cash",
            "reminders",
            "reports",
            "subscription",
            "clinic-settings",
            "clinic-audit",
            "push",
        ],
    },
    {
        "name": "Публичное и служебное",
        "tags": ["catalog", "leads", "clinic-applications", "payments-webhook", "health"],
    },
]


def _warn_missing_cabinet_url() -> None:
    """FreedomPay настроен, а адрес кабинета — нет: оплата пройдёт, но вслепую.

    Тихая поломка хуже громкой: плательщик остаётся на странице провайдера (адрес
    возврата не передаётся), в сообщениях о подписке нет ссылки на оплату, в SMS
    с кодом — привязки к домену. Строка в логе на старте — единственный сигнал.
    """
    payments_on = bool(settings.freedompay_merchant_id and settings.freedompay_secret_key)
    if payments_on and not settings.cabinet_url:
        structlog.get_logger(__name__).warning(
            "cabinet_url_missing",
            hint="задайте CABINET_URL: без него FreedomPay не вернёт плательщика в кабинет",
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Жизненный цикл: логи, очередь задач и слушатель реалтайм-шины."""
    configure_logging()
    _warn_missing_cabinet_url()
    await connect_job_queue()
    # Слушатель шины pg_notify → комнаты Socket.IO ЭТОГО процесса (Ф4).
    # Стартует здесь, потому что socketio.ASGIApp пробрасывает lifespan сюда.
    realtime_listener = asyncio.create_task(run_realtime_listener(sio))
    yield
    realtime_listener.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await realtime_listener
    await close_job_queue()
    await redis_client.aclose()
    await close_shared_clients()  # Ф8: общие httpx-пулы (Meta, Google)
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
        # Сервис может стоять за прокси под префиксом (ROOT_PATH=/skrz): тогда
        # Swagger/ReDoc запрашивают /skrz/openapi.json, а не /openapi.json —
        # иначе спека уедет в соседний сервис, живущий на том же домене.
        root_path=settings.root_path,
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
    app.include_router(onboarding_router, prefix=settings.api_v1_prefix)
    app.include_router(leads_router, prefix=settings.api_v1_prefix)
    app.include_router(clinic_applications_router, prefix=settings.api_v1_prefix)
    app.include_router(clinic_applications_admin_router, prefix=settings.api_v1_prefix)
    app.include_router(tariffs_router, prefix=settings.api_v1_prefix)
    app.include_router(clients_router, prefix=settings.api_v1_prefix)
    app.include_router(accounts_router, prefix=settings.api_v1_prefix)
    app.include_router(staff_router, prefix=settings.api_v1_prefix)
    app.include_router(schedule_router, prefix=settings.api_v1_prefix)
    app.include_router(patients_router, prefix=settings.api_v1_prefix)
    app.include_router(services_router, prefix=settings.api_v1_prefix)
    app.include_router(appointments_router, prefix=settings.api_v1_prefix)
    app.include_router(cash_router, prefix=settings.api_v1_prefix)
    app.include_router(reminders_router, prefix=settings.api_v1_prefix)
    app.include_router(reports_router, prefix=settings.api_v1_prefix)
    app.include_router(subscription_router, prefix=settings.api_v1_prefix)
    app.include_router(clinic_settings_router, prefix=settings.api_v1_prefix)
    app.include_router(roles_router, prefix=settings.api_v1_prefix)
    app.include_router(reports_admin_router, prefix=settings.api_v1_prefix)
    app.include_router(access_admin_router, prefix=settings.api_v1_prefix)
    app.include_router(audit_router, prefix=settings.api_v1_prefix)
    app.include_router(clinic_audit_router, prefix=settings.api_v1_prefix)
    app.include_router(jobs_router, prefix=settings.api_v1_prefix)
    app.include_router(subscription_admin_router, prefix=settings.api_v1_prefix)
    app.include_router(payments_router, prefix=settings.api_v1_prefix)
    app.include_router(dashboard_router, prefix=settings.api_v1_prefix)
    app.include_router(push_router, prefix=settings.api_v1_prefix)
    app.include_router(whatsapp_router, prefix=settings.api_v1_prefix)
    app.include_router(whatsapp_admin_router, prefix=settings.api_v1_prefix)
    app.include_router(usage_router, prefix=settings.api_v1_prefix)
    app.include_router(ai_router, prefix=settings.api_v1_prefix)

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
#: Точка запуска сервера: FastAPI + Socket.IO-шлюз на ``/socket.io/`` (Ф4).
#: ``app`` остаётся чистым FastAPI — его используют тесты и генератор OpenAPI.
asgi_app = build_asgi(app)
