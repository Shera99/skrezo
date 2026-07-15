# Skrezo Platform — Архитектура

Версия: 0.1 · Модульный монолит на FastAPI + PostgreSQL + Redis с элементами
DDD. Документ фиксирует принципы, границы модулей, модель данных и план работ.

## 1. Принципы

- **Единый контур авторизации.** Один вход по **телефону + паролю** (argon2) для
  всех — команда Skrezo и сотрудники клиник (`POST /auth/login`). Один JWT-секрет
  и `aud`. Доступ разграничивают роли: claim `platform_admin`
  (`account.is_platform_admin`) → `/admin/*`; членство в клинике + RBAC → `/clinic/*`.
  Вход **пациентов** по OTP относится к их приложению (glo) и в этом сервисе не
  реализуется.
- **Изоляция тенантов.** Клиничные эндпоинты строго ограничены `clinic_id`
  из токена; платформенные (`/admin/*`) — кросс-тенантные (только `platform_admin`).
- **Деньги — целые сомы** (`integer`). **Идентификаторы — UUID v4**. **Время
  — `timestamptz`** (серверные `now()`).
- **Биллинг — по дате собственного продления клиента**, не единый календарный
  месяц для всех.
- **Справочники и версии тарифов** не удаляются (нужны как основание для
  прошлых платежей / истории).

## 2. Слои модуля (обязательные конвенции)

Каждый модуль — ограниченный контекст со строгой DDD-слоёвкой
`presentation → application → infrastructure → domain`:

| Слой | Содержимое |
|---|---|
| `presentation/` | `routes/` (**один эндпоинт = один файл**, `__init__` собирает `APIRouter`), `schemas.py` (Pydantic DTO), `deps.py` (DI). Роут зовёт **один** use-case |
| `application/` | `use_cases/` (**одна задача = один use-case**, по файлу) + `services/` (переиспользуемая логика, напр. `token_service`) |
| `infrastructure/` | `repositories/` — доступ к данным поверх общих ORM-моделей; адаптеры провайдеров |
| `domain/` | перечисления, value-objects, доменные данные (НЕ ORM) |

Поток вызова: **route → use_case → service/repository**. Репозитории
инкапсулируют SQL; use-case оркестрирует; service — переиспользуемое.

**Все ORM-модели — в `app/shared/db/models/`, по файлу на таблицу** (таблицы
переиспользуются между модулями; репозитории их импортируют, а не владеют).
Все модели делят один `Base.metadata` (общая схема для Alembic).

## 3. Карта модулей

Таблицы — в `app/shared/db/models/`; столбец «Таблицы» показывает, с какими из
них работает модуль.

| Модуль | Назначение | Таблицы (в shared/db/models) | Фаза |
|---|---|---|---|
| `core` / `shared` | инфраструктура, общий слой, **все ORM**, i18n | все | 1 ✅ |
| `catalog` | справочники город/специализация (i18n) | `city(+_translation)`, `specialization(+_translation)` | 1 ✅ |
| `access` | RBAC клиники: права/роли (пер-клиника) | `permission`, `role`, `role_permission` | 2a ✅ |
| `platform_auth` | только гвард `/admin/*` (требует `platform_admin`); собственного входа нет | — (флаг `account.is_platform_admin`) | 2a ✅ |
| `clinics` | провижининг (ручное, командой Skrezo; владельцу — временный пароль); роутов не публикует — создание аккаунта под `/admin/clients` | `account`, `membership`, `clinic` (в shared) | 3 ✅ |
| `clients` | админ: список (offset-пагинация)/карточка/создание/правка, врачи (админ-сайд), phone-check, счётчики статусов, смена номера, архивация | clinic/account/membership/subscription (в shared) | 6 ✅ |
| `staff` | клиент-сайд: сотрудники клиники (RBAC; работнику — временный пароль) | membership/account/role (в shared) | 6b ✅ |
| `identity` | **единый вход** всех (телефон + пароль), смена пароля, выбор клиники | `account` (в shared) | 2b ✅ |
| `leads` | лид-форма «Больше одного» + Telegram-нотификация | `lead` | 3a ✅ |
| `tariffs` | версии тарифов: solo (цена), clinic (пакеты + `over_price` «Сеть»); расчёт цены, история | `tariff_version`, `tariff_package` | 4a ✅ |
| `payments` | журнал платежей + сводка, refund (v1 — заглушки формы) | — (биллинг отложен) | 5 (заглушка) |
| `dashboard` | сводка за период + тренды по месяцам (active/trial/риски/конверсия/отмены; заявки→клиенты; MRR ждёт биллинг) | читает clinic/subscription/lead | 7 ✅ |
| `subscriptions` | подписка, статусы, триал | `subscription`, `subscription_event` | 3a ✅ (триал) / 4 |
| `billing` | платежи, карта, refund, джобы | `platform_payment`, `payment_method` | 5 |
| `dashboard` | сводки и тренды | — (агрегаты + кеш) | 7 |
| `audit` | журнал мутаций /admin/* (запись + чтение) | `audit_log` | 7 ✅ |
| `notifications` | каналы SMS/Telegram/WhatsApp | — | 2+ |

## 4. Модель данных

### 4.1. Реализовано (фазы 1–3a)

- **city / specialization** — `id, sort_order, active`. Название вынесено в
  таблицы переводов. Правятся только миграцией/сидом.
- **city_translation / specialization_translation** — `id, <entity>_id, lang,
  value, unique(<entity>_id, lang)`. Переводы названий (kg/kz/ru/en).
- **account** — `id, phone(unique, E.164), full_name, lang, onboarded,
  created_at, updated_at`. Личность клиента, телефон = логин.
- **clinic** — `id, name, type(solo|clinic), city_id?→city (для соло — NULL до
  онбординга), address?, business_phone?, lang, seats_limit?(clinic),
  support_notes?, timestamps`.
- **membership** — `id, clinic_id→clinic, account_id→account, role_id→role,
  specialization_id?→specialization, active, created_at, updated_at`.
  «Один владелец на клинику» — гарантируется прикладным слоем.
- **permission** — `id, key(unique), group_key, description, sort_order`.
  Системный каталог гранулярных прав (сид, 25 шт.), клиентом не редактируется.
- **role** — `id, clinic_id?(NULL=глоб. шаблон), key?, name, is_system,
  is_owner, timestamps`. Пер-клиника; базовые шаблоны Владелец/Менеджер/Работник.
- **role_permission** — `(role_id, permission_id)` — включённые права роли
  (это и есть «настройка прав в UI»).
- **account** — единая таблица всех пользователей: `id, phone(unique=логин),
  full_name, lang, onboarded, password_hash?, must_change_password,
  is_platform_admin, timestamps`. Команда Skrezo = аккаунт с `is_platform_admin`
  (без клиничного членства). Таблица `platform_user` удалена (миграция 0017).
- **lead** — `id, full_name, phone(unique=дедуп), clinic_name,
  doctors_count_range?, comment?, converted_clinic_id?→clinic, created_at`.
- **subscription** — `id, clinic_id(unique), status, trial_started_at,
  trial_ends_at, card_attach_deadline?(гейт карты отключён → NULL),
  card_attached_at?, current_period_start?, current_period_end?, cancelled_at?,
  timestamps`. Статусы: trial/awaiting_card/active/past_due/blocked/cancelled
  (сейчас используется только `trial`).
- **subscription_event** — `id, subscription_id→, from_status?, to_status,
  reason?, created_at`. Лог переходов статуса.
- **audit_log** — `id, actor_id→account, action, entity_type, entity_id?,
  payload(jsonb)?, created_at`. Неизменяемый лог мутаций `/admin/*`; пишется из
  роутов после успешной мутации (`AuditService.record`, отдельная транзакция).
  Чтение — `GET /admin/audit` (фильтры actor/clinicId/from/to + keyset-курсор).
- **tariff_version** — `id, type(solo|clinic), version(int), price?(solo),
  over_price?(clinic «Сеть»), created_at, created_by→account`. Версионирование
  без удаления; текущая = max(version).
- **tariff_package** — `id, tariff_version_id→, name, up_to, price`. Пакеты
  клиники (фикс. цена до up_to мест). Расчёт — `compute_clinic_price`
  (ступенчато, монотонно). Валидация: непрерывность, первая от ≤2, последняя без границы.

> Базовые `account`/`clinic`/`membership` **реконструированы из admin-спеки**
> (исходный BACKEND_SPEC.md недоступен — относился к мобильному приложению
> фронтенд-разработчика). При появлении общей БД мобильного бэкенда — состыковать.

### 4.2. Плановые таблицы

| Таблица | Модуль | Ключевые поля |
|---|---|---|
| `platform_payment` | billing | clinic_id→, amount, status(success|failed|refunded), period_start, period_end, refund_reason?, created_at |
| `payment_method` | billing | clinic_id→, provider_token, brand?, last4?, created_at |

Статусы подписки: `trial`, `awaiting_card`, `active`, `past_due`, `blocked`,
`cancelled`.

## 5. Аутентификация

- **Единый вход** (`identity`) ✅: телефон + пароль (argon2) для всех —
  `POST /auth/login|change-password|refresh|logout|select-clinic`, `GET /auth/me`.
  Токен несёт `account_id` + claims: `platform_admin` (команда Skrezo) и/или
  `clinic_id`/`role_id` (скоуп выбранной клиники). Первичный пароль владельцу/
  работнику выдаётся **временным** (`must_change_password=true`): `login` вернёт
  `mustChangePassword=true`, нужно вызвать `change-password`. Мультиклиничный
  аккаунт выбирает клинику через `select-clinic`. Refresh-токены — в Redis
  (allowlist/revoke). Регистрацию клиник ведёт только команда Skrezo; self-service
  соло-регистрации нет. Вход пациентов по OTP — в приложении glo, вне сервиса.
- **Доступ к `/admin/*`** (`platform_auth` — только гвард) ✅: зависимость
  `get_authenticated_user_id` поверх общего контекста требует `platform_admin`
  (иначе 403 `not_platform_admin`) и возвращает `account_id` (actor аудита).
  Собственного входа модуль больше не имеет.

Примитивы — в `app/core/security.py` (`create_token`, `decode_token`,
`hash_password`, `verify_password`, `generate_temp_password`). Админы Skrezo
заводятся скриптом `python -m app.scripts.create_platform_user --phone … --password …`.

- **Rate-limit** (`app/shared/api/rate_limit.py`, счётчики в Redis, фикс. окно →
  429 `too_many_requests` c `retryAfter`): вход (по IP+телефон), смена пароля,
  лид-форма (по IP). Лимиты — в настройках (`LOGIN_RATE_LIMIT` и др.).
- **Сброс пароля** инициирует Skrezo/владелец (не self-service): `POST
  /admin/clients/{id}/reset-password` (владельцу) и `POST
  /clinic/staff/{id}/reset-password` (работнику) → новый временный пароль
  (`must_change_password=true`). Самообслуживаемого «забыл пароль» нет — нужен
  канал доставки кода (SMS/WhatsApp, не выбран).

### 5.1. RBAC клиники (модуль `access`)

Роли — **на стороне клиники**, права **гранулярные и настраиваемые пер-клиника**:

- **Каталог прав** (`permission`) — системный, ключи вида `patients.edit`.
- **Роли** (`role`) — базовые шаблоны (Владелец `is_owner` / Менеджер /
  Работник) плюс кастомные; у каждой клиники — свои роли (клонируются из
  глобальных шаблонов при создании клиники, фаза 3).
- **Владелец** — полный доступ, права не редактируются. **Skrezo `admin`** —
  минует проверки прав (полный доступ надо всеми клиниками).
- Клиентский токен несёт `clinic_id` + `role_id`; `PermissionResolver` резолвит
  права роли (кеш в Redis), guard `require_permission("key")` их проверяет ✅
  (владелец минует проверку). Контекст токена — `app/shared/auth`, чтобы
  `identity` и `access` не зависели циклически.
- **Сотрудники (client-side, модуль `staff`):** заводить/вести работников и
  менять их телефон — право `staff.manage` (владелец + менеджер). **Работник
  свой номер менять не может** (нет `staff.manage`). Владельца через staff-флоу
  не править/не удалять. Соло сотрудников не имеет.

## 6. Redis

Кеш справочников (по языку) и агрегатов дашборда · rate-limit публичных ручек ·
refresh-токены (allowlist) · проверка `subscription.status` на горячем пути
клиентских запросов · брокер и локи Arq.

## 6.1. Мультиязычность (i18n)

Языки: `kg`, `kz`, `ru`, `en` (`Lang`, дефолт `ru`). Два пласта:

- **БД-контент** (названия справочников) — таблицы переводов на сущность
  (`<entity>_translation`, стандарт Globalize/parler): индексируется по языку,
  масштабируется, чист для поиска/сортировки. Чтение — `coalesce(язык, ru)`.
- **Системные строки** (роли, шаблоны уведомлений) — каталог в коде
  `app/shared/i18n/messages.py` с фолбэком на `ru`.

Язык запроса: `?lang=` → `Accept-Language` → `account.lang` → `ru`
(зависимость `get_request_lang`). Кеш справочников — по ключу с языком.
⚠️ `ru`/`en` заполнены; `kg`/`kz` — заглушки под вычитку носителями.

## 7. Биллинг

### 7.0. Текущее правило (упрощение на старте)

**Автоматический биллинг отложен.** Решение: **1 месяц бесплатно всем, полный
функционал, без карты**; тарифы Skrezo проставляет/ведёт вручную из админки
(каталог — модуль `tariffs`, фаза 4a). Поэтому НЕ реализуем: провайдера оплаты,
привязку карты, автосписания, 7-дневный гейт карты (`awaiting_card`), крон-джобы
продления. Триал = `trial_started_at .. +30 дней`, `card_attach_deadline = NULL`,
проверки статуса на клиентских ручках нет (полный доступ). Arq-воркер — заглушка.

### 7.1. Будущий автобиллинг (когда вернётся)

Периодический скан (Arq cron) по индексам дат вместо таймера на подписку:

- `card_attach_deadline` прошёл без карты → `awaiting_card`.
- `trial_ends_at` наступил → списание → `active` / `past_due`.
- `current_period_end` наступил → `compute_price` → продление / `past_due`
  (без grace-period и автоповторов; разблокировка вручную `/billing/pay-now`).

`compute_price`: solo — фикс. цена последней версии; clinic — **ступенчатая**
сумма по активным `doctor`-местам (`compute_clinic_price`, готов в фазе 4a).
Версия тарифа — по правилу 7-дневного окна: `created_at ≤ current_period_end − 7 дней`.

## 8. Конвенции (обязательные)

- **DDD-слои в модуле:** `presentation` (routes/schemas/deps) → `application`
  (use_cases + services) → `infrastructure` (repositories) → `domain`. Поток:
  route → use_case → service/repository. Один эндпоинт = один файл; одна
  задача = один use-case.
- **Все ORM — в `app/shared/db/models/`, одна таблица = один файл.** Доступ к
  ним — через репозитории в `infrastructure/`. Новая таблица → новый файл +
  импорт в `app/shared/db/models/__init__.py`.
- Миграции — инкрементально (не один гигантский initial).
- Все ошибки — наследники `AppError`; тело — `{ "error": code, "message", ... }`.
- Комментарии/докстринги — на русском; образцовая OpenAPI-документация.
- Пользовательский текст — через i18n (переводы в БД / каталог сообщений).
- Телефоны нормализуются в E.164 (`app/shared/domain/value_objects.py`) до
  любого сравнения/вставки (единое правило дедупликации).

## 9. Открытые вопросы (из спек)

1. Провайдер платежей (КГ) не выбран — `payment_method.provider_token` и
   `refund` пока заглушки.
2. `lifetimeRevenue` в списке клиентов — LTV Skrezo или выручка кассы клиники?
   До решения возвращаем `null`.
3. Реактивация после отмены — отдельный эндпоинт или ручной процесс поддержки.
4. Канал доставки временного пароля владельцу/работнику (сейчас возвращается в
   ответе API; WhatsApp/SMS-провайдер КГ — не выбран).
5. **Self-service «забыл пароль»** — не реализован (нужен канал доставки кода).
   Сейчас сброс — только инициируемый Skrezo/владельцем (новый временный пароль).
