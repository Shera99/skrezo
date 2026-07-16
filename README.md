# Skrezo — Platform API

Бэкенд **платформенной зоны Skrezo** (админка команды) и **входа сотрудников
клиник** для стоматологической платформы. Сервис отвечает за провижининг
клиник и соло-врачей, заявки, тарифы, подписки и биллинг Skrezo ↔ клиника,
платежи и дашборд, а также за вход сотрудников клиник (владелец/менеджер/
работник) в рабочее приложение по **телефону и паролю**.

> **Клиническое ядро — источник правды здесь.** Пациент **глобальный** (общий на
> всю платформу), история лечения **сквозная по всем клиникам**; врач = наш
> `membership`. Модули `patients`/`services`/`appointments`. Касса и расписание —
> следующие итерации. Вход **пациентов** по OTP — в приложении glo, здесь не
> реализуется.

## Область (scope)

| В скоупе | Вне скоупа |
|---|---|
| Платформенная зона `/admin/*` (клиенты, тарифы, подписки, платежи, дашборд, аудит) | Касса/услуги/расписание клиники (следующие итерации) |
| Клиническое ядро: глобальные пациенты, одонтограмма (FDI), услуги, приёмы, сквозная история зуба | Мобильное приложение пациента glo (вход пациентов по OTP) |
| Вход сотрудников клиник по телефону + паролю (временный пароль → смена при 1-м входе) | Вход пациентов по OTP (приложение glo, вне сервиса) |
| Провижининг: лид-форма, ручное создание (клиники заводит только команда Skrezo) | Кабинет клиники (веб-офис) — отдельная фаза; роли команды Skrezo (v2) |
| Триал 1 мес (бесплатно всем, без карты), тарифы (каталог, ступени) | Автобиллинг: карта, списания, продление, платёжный провайдер |

## Архитектура

Модульный монолит с элементами DDD. Каждый модуль — ограниченный контекст со
слоями `domain` / `application` / `infrastructure` / `api`. Подробно —
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

- **FastAPI** (async) + **Uvicorn**
- **PostgreSQL** + **SQLAlchemy 2.0 (async)** + **asyncpg** + **Alembic**
- **Redis** — кеш, rate-limit, refresh-токены (allowlist), брокер задач
- **Arq** — фоновые биллинг-джобы
- **PyJWT** (единый контур токенов), **argon2** (пароли всех аккаунтов)

## Требования

- Python **3.12+** (проект запинен на 3.12 через `.python-version`)
- [uv](https://docs.astral.sh/uv/) — менеджер зависимостей
- Docker + Docker Compose (для Postgres/Redis и полного стека)

## Запуск

### Через Docker (весь стек)

```bash
cp .env.example .env
make up            # соберёт образ, поднимет postgres+redis+api+worker, прогонит миграции
# API:     http://localhost:8000
# Swagger: http://localhost:8000/docs
```

### Нативно (для разработки)

```bash
cp .env.example .env
make install       # uv sync
make infra         # поднять только postgres + redis в docker
make migrate       # применить миграции (создаст таблицы + сид справочников)
make run           # uvicorn с автоперезагрузкой
```

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `APP_ENV` | `local` / `staging` / `production` | `local` |
| `APP_DEBUG` | подробные логи, echo SQL | `true` |
| `API_V1_PREFIX` | префикс версии API | `/api/v1` |
| `CORS_ORIGINS` | разрешённые домены (через запятую) | — |
| `DATABASE_URL` | DSN PostgreSQL (asyncpg) | `postgresql+asyncpg://skrezo:skrezo@localhost:5432/skrezo` |
| `REDIS_URL` | DSN Redis | `redis://localhost:6379/0` |
| `JWT_SECRET` | секрет единого контура токенов | — |
| `ACCESS_TOKEN_TTL_SECONDS` | срок жизни access-токена | `900` |
| `REFRESH_TOKEN_TTL_SECONDS` | срок жизни refresh-токена | `2592000` |
| `TELEGRAM_TEAM_BOT_TOKEN` / `TELEGRAM_TEAM_CHAT_ID` | нотификации команды о лидах | — |
| `TRIAL_DAYS` / `CARD_ATTACH_DEADLINE_DAYS` | правила триала | `30` / `7` |

## Документация API

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI JSON: `GET /openapi.json`

## Эндпоинты

Пути даны с префиксом `/api/v1` (кроме `/health`). Админ-панель (веб-офис
команды) реализована по контрактам фронта 1:1.

**Служебное и аутентификация**

Вход **единый** для всех (команда Skrezo и сотрудники клиник) — телефон + пароль,
один JWT-контур. Доступ разграничивают роли: `isPlatformAdmin` → админ-панель
`/admin/*`; членство в клинике + права (RBAC) → `/clinic/*`.

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/health` | Проверка живости |
| `GET` | `/cities` · `/specializations` | Справочники (публичные) |
| `GET` | `/admin/refs/cities` · `/admin/refs/specializations` | Справочники для админки |
| `POST` | `/auth/login` | Единый вход по телефону + паролю (в ответе `isPlatformAdmin`) |
| `POST` | `/auth/change-password` | Смена пароля (в т.ч. обязательная при 1-м входе) |
| `POST` | `/auth/select-clinic` · `/refresh` · `/logout` | Выбор клиники, токены, выход |
| `GET` | `/auth/me` | Профиль + активная клиника + права + `isPlatformAdmin` |
| `POST` | `/leads` | Лид-форма «Больше одного» (публичная) |

**Клиенты (админ-панель)**

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/admin/clients` | Список: фильтры `type/status/query/cities[]/tariffs[]`, `page` (25/стр), `totalCount` |
| `POST` | `/admin/clients` | Создать аккаунт (триал 30 дней; вернёт `tempPassword` владельца) |
| `GET` | `/admin/clients/phone-check?phone=` | Свободен номер или занят (`ownerName`) |
| `GET` | `/admin/clients/status-counts` | Число клиентов по статусам подписки |
| `GET` | `/admin/clients/{id}` | Карточка (сотрудники, подписка, платежи, заметки) |
| `PATCH` | `/admin/clients/{id}` | Правка карточки (клиника + владелец) |
| `POST` | `/admin/clients/{id}/admin-phone` | Смена номера админа (напрямую, без OTP) |
| `POST` | `/admin/clients/{id}/reset-password` | Сброс пароля владельца → новый `tempPassword` |
| `POST` | `/admin/clients/{id}/archive` | Архивация (→ cancelled) |
| `POST` | `/admin/clients/{id}/doctors` | Добавить врача (занимает место; `tempPassword`) |
| `PATCH` | `/admin/clients/{id}/doctors/{docId}` | Правка врача |
| `DELETE` | `/admin/clients/{id}/doctors/{docId}` | Удалить врача (деактивация) |

**Тарифы · Платежи · Дашборд (админ-панель)**

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/admin/tariffs` | Бандл: solo, clinic (пакеты + `overPrice`), история версий |
| `POST` | `/admin/tariffs/solo` | Новая версия соло `{price}` |
| `POST` | `/admin/tariffs/clinic` | Новая версия клиники `{packages, overPrice}` (валидация → 422; `affectedClients`) |
| `GET` | `/admin/payments` | Журнал + сводка за период (v1 — заглушка: пусто/нули) |
| `POST` | `/admin/payments/{id}/refund` | Пометка возврата (v1 — заглушка → 404) |
| `GET` | `/admin/dashboard/summary` | Метрики за период (`from`/`to`): active/trial/риски/конверсия/отмены |
| `GET` | `/admin/dashboard/trends?months=6` | Ряды по месяцам: MRR (0), активные (solo/clinic), заявки→клиенты, срез solo/clinic |

**Сотрудники (клиент-сайд, клиентский токен + RBAC)**

| Метод | Путь | Описание |
|---|---|---|
| `GET` · `POST` | `/clinic/staff` | Список / добавить работника (`staff.view` / `staff.manage`) |
| `PUT` · `DELETE` | `/clinic/staff/{id}` | Правка / удаление работника (`staff.manage`) |
| `POST` | `/clinic/staff/{id}/reset-password` | Сброс пароля работника → `tempPassword` (`staff.manage`) |

**Клиническое ядро (клиент-сайд + RBAC).** Пациент **глобальный** (общий на всю
платформу, phone UNIQUE); клиника видит его при наличии приёма. История зуба —
**сквозная по всем клиникам**. Врач = наш `membership`.

| Метод | Путь | Описание |
|---|---|---|
| `GET` | `/clinic/patients` | Пациенты этой клиники (у кого есть приём), поиск/пагинация (`patients.view`) |
| `GET` | `/clinic/patients/lookup?phone=` | Глобальный поиск по телефону для привязки (`patients.edit`) |
| `POST` | `/clinic/patients` | Создать глобального пациента (409 с `patientId`, если телефон занят) |
| `GET` · `PATCH` | `/clinic/patients/{id}` | Карточка + одонтограмма / правка (нужна связь с клиникой) |
| `PUT` | `/clinic/patients/{id}/teeth` | Создать/обновить одонтограмму (карта зубов, FDI) |
| `GET` | `/clinic/patients/{id}/teeth/{fdi}` | Сквозная история зуба (`patients.view_medical`) |
| `GET` · `POST` · `PATCH` | `/clinic/services[/{id}]` | Прайс клиники: список/создать/правка (`price.view`/`price.edit`) |
| `GET` · `POST` | `/clinic/appointments` | Приёмы клиники / создать с манипуляциями по зубам (`patients.view_medical`/`appointments.create`) |
| `GET` · `PATCH` | `/clinic/appointments/{id}` | Детали / обновить статус (`patients.view_medical`/`appointments.edit`) |

**Защита (rate-limit).** На Redis, фиксированное окно, 429 `too_many_requests` с
`retryAfter`: вход (`/auth/login`, по IP+телефон), смена пароля, лид-форма (по IP).
Лимиты — в переменных окружения (`LOGIN_RATE_LIMIT` и др.).

> Сброс пароля инициирует Skrezo/владелец (новый временный → смена). **Самообслуживаемого
> «забыл пароль» нет** — нужен канал доставки кода (SMS/WhatsApp, не выбран).
>
> Отложено (зависит от биллинга): `POST /admin/billing/pay-now`, экспорт
> платежей `GET /admin/payments/export`. Метрики `mrr`/`revenue`/`lifetimeRevenue`
> и блок активности соло — заглушки (0/null), пока биллинг вне скоупа.
| `GET` | `/api/v1/admin/audit` | Журнал аудита (фильтры actor/clinicId/from/to) |

Создать админа Skrezo: `make create-admin phone=0700000001 password=secret`.
Тестовая клиника (dev): `uv run python -m app.scripts.create_test_clinic --phone 0700123456`.

## Мультиязычность

Языки: `kg`, `kz`, `ru`, `en` (дефолт `ru`). Язык ответа: `?lang=en` или заголовок
`Accept-Language`. Пример: `GET /api/v1/cities?lang=en` → `Bishkek`; без параметра
→ `Бишкек`. Названия справочников — в таблицах переводов; `ru`/`en` заполнены,
`kg`/`kz` — заглушки под вычитку носителями.

## Формат ответа и ошибок

Успех — тело ресурса как есть. Ошибка — «плоский конверт»:

```json
{ "error": "lead_already_exists", "message": "...", "createdAt": "..." }
```

## Тесты

```bash
make test          # pytest
make check         # ruff + mypy + pytest
```

- **Юнит-тесты** (`tests/*.py`) — без внешних зависимостей (крипто, пагинация,
  ценообразование, i18n, схемы).
- **Интеграционные API-тесты** (`tests/api/*.py`) — httpx поверх ASGI на
  **эфемерном Postgres** (`testcontainers`): реальные миграции + сиды, `get_redis`
  подменён на `fakeredis`. **Требуется запущенный Docker.** Между тестами
  мутируемые таблицы чистятся, справочники/шаблоны ролей — нет. Фикстуры —
  `tests/conftest.py` (`admin_client` — авторизованный платформенный админ).
- **CI** — `.github/workflows/ci.yml`: `ruff` + `mypy` + `pytest` на каждый
  push в `main` и PR (Docker на `ubuntu-latest` доступен для testcontainers).

## Структура проекта

Строгие конвенции DDD: **presentation → application → infrastructure → domain**,
все **ORM в `shared/db/models`** (одна таблица = один файл), доступ — через
репозитории. Подробнее — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §2, §8.

```
app/
  core/                 # config, db, redis, security(JWT), errors, logging
  shared/
    db/models/          # ВСЕ ORM-модели, по файлу на таблицу (account.py, city.py, …)
    db/base.py|enums.py # Base, миксины, enum-типы (Lang, ClinicType)
    i18n/               # резолв языка запроса + каталог сообщений
    api/                # базовые схемы (CamelModel)
  modules/<module>/
    presentation/       # routes/ (1 эндпоинт = 1 файл), schemas.py, deps.py
    application/        # use_cases/ (1 задача = 1 use_case), services/
    infrastructure/     # repositories/ (доступ к данным поверх shared ORM)
    domain/             # enums, доменные данные (не ORM)
  jobs/                 # arq-воркер (биллинг)
alembic/                # миграции
docs/                   # ARCHITECTURE.md
```

Реализованные модули: `catalog` (справочники, i18n), `access` (RBAC),
`platform_auth` (вход Skrezo). Остальные — по фазам.

## Дорожная карта (фазы)

1. ✅ Каркас: core, docker, Alembic, базовые таблицы + справочники
2. Аутентификация: ✅ 2a — RBAC + платформенный вход; ✅ 2b — вход сотрудников клиник (телефон + пароль) + require_permission
3. ✅ Провижининг: лиды + ручное создание клиник командой Skrezo + подписка-триал (владельцу выдаётся временный пароль)
4. Тарифы: ✅ версии/ступени + `compute_price` (ведём вручную из админки). Триал — 1 мес бесплатно всем, без карты; автобиллинг/карта/списания — отложены
5. Биллинг-джобы (Arq) + платежи
6. ✅ Клиенты: список/карточка/правка, сотрудники (RBAC, client-side; временный пароль работнику), смена номера админа (напрямую), архивация
7. ✅ Аудит (журнал мутаций `/admin/*`); ✅ дашборд: сводка + тренды по месяцам (MRR ждёт биллинг)
8. Кабинет клиники (после пересмотра ТЗ)
9. ✅ Клиническое ядро v2: глобальный пациент, одонтограмма, услуги (прайс), приёмы, сквозная история зуба. Далее — касса (оплата приёмов) и расписание
