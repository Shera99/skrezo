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
| `clinics` | провижининг (ручное, командой Skrezo; владельцу — временный пароль) + **настройки своей клиники** `/clinic/settings` (название, город, контакты, язык, таймзона; права `settings.view`/`settings.edit`) | `account`, `membership`, `clinic` (в shared) | 3 ✅ / 14 ✅ |
| `clients` | админ: список (offset-пагинация)/карточка/создание/правка, врачи (админ-сайд), phone-check, счётчики статусов, смена номера, архивация | clinic/account/membership/subscription (в shared) | 6 ✅ |
| `staff` | клиент-сайд: сотрудники клиники (RBAC; работнику — временный пароль) | membership/account/role (в shared) | 6b ✅ |
| `identity` | вход: сотрудники клиник — телефон + пароль, соло-врачи — OTP (passwordless) + self-service регистрация с триалом; смена пароля своим паролём и **self-service «забыл пароль»** по коду (`/auth/password/forgot`+`/reset`, отзыв сессий), выбор клиники. **Доставка OTP** — цепочка каналов `infrastructure/otp` (Telegram Gateway → WhatsApp → SMSPRO) с переходом к следующему при неудаче; назначение кода (`login`/`reset`) — часть ключа, коды не взаимозаменяемы | `account` (в shared) | 2b ✅ / 12 / 14 ✅ |
| `access` | RBAC-каталог/резолвер прав, guard `require_permission`; **управление ролями клиники** (`/clinic/roles`, право `roles.manage`); **триал-политика** (`permission.in_trial`) — ограничение функций на триале + админ-настройка `/admin/trial-features` | `permission`, `role`, `role_permission` | 2a ✅ / 12 ✅ / 13 ✅ |
| `leads` | лид-форма «Больше одного» + нотификация команды в Telegram-чат | `lead` | 3a ✅ / 14 ✅ |
| `clinic_applications` | заявки клиник (2-й путь регистрации после OTP): подача с подтверждённым телефоном + рассмотрение в админке (статусы pending/reviewed/rejected). Клиника заводится вручную | `clinic_application` | 12 ✅ |
| `tariffs` | версии тарифов: solo (цена), clinic (пакеты + `over_price` «Сеть»); расчёт цены, история | `tariff_version`, `tariff_package` | 4a ✅ |
| `payments` | журнал платежей + сводка, refund (v1 — заглушки формы) | — (биллинг отложен) | 5 (заглушка) |
| `dashboard` | сводка за период + тренды по месяцам (active/trial/риски/конверсия/отмены; заявки→клиенты; MRR ждёт биллинг) | читает clinic/subscription/lead | 7 ✅ |
| `subscriptions` | подписка, статусы, триал и оплаченный период; клиентская ручка `GET /clinic/subscription` + админские `POST /admin/clients/{id}/subscription/activate|block` (оплата приходит переводом, отмечает человек); **правило доступа** (`domain/access`: режим чтения считается по датам `trial_ends_at` и `current_period_end`, а не по статусу); джобы `expire_trials`, `expire_paid_periods`, `warn_trial_ending` и `warn_period_ending` (предупреждение однократно — отметка ставится тем же UPDATE; у периода сбрасывается при продлении) | `subscription`, `subscription_event` | 3a ✅ (триал) / 13 ✅ / 14 ✅ / 4 |
| `billing` | платежи, карта, refund, джобы | `platform_payment`, `payment_method` | 5 |
| `dashboard` | сводки и тренды | — (агрегаты + кеш) | 7 |
| `audit` | журнал мутаций: платформенный (`/admin/audit`, действия Skrezo) и **клиничный** (`/clinic/audit`, право `audit.view`) — все мутации клиники. Разделены колонкой `audit_log.clinic_id`: у платформенных она пуста | `audit_log` | 7 ✅ / 15 ✅ |
| `patients` | клиническое ядро: **глобальные** пациенты, одонтограмма (FDI), сквозная история зуба, **файлы карты** (снимки и документы на диске; тип по содержимому, раздача только через ручку) (RBAC `patients.*`) | `patient`, `patient_tooth`, `patient_file` | 9 ✅ / 15 ✅ |
| `services` | прайс клиники (услуги, цены) (RBAC `price.*`) | `service` | 9 ✅ |
| `appointments` | приёмы (клиника+врач=membership+пациент) и **чек списком позиций**: услуга, цена за единицу, количество, зубы (RBAC `appointments.*`) | `appointment`, `appointment_item`, `appointment_item_tooth` | 9 ✅ / 14 ✅ |
| `schedule` | расписание: лента дня, свободные слоты, часы клиники и графики врачей; владеет `AvailabilityService` — общим определением «врача» и правилами доступности (его же зовёт `appointments`) (RBAC `schedule.*`/`settings.*`) | `clinic_working_hours`, `doctor_schedule`, `doctor_time_off`, `clinic.timezone` | 10 ✅ |
| `cash` | касса клиники: платежи/возвраты по приёмам, долги (по приёму), сводка; владеет `CashService` — правилами проведения оплаты (его же зовёт `appointments` при завершении) (RBAC `cash.*`) | `cash_payment` | 11 ✅ |
| `reports` | операционная аналитика: выручка, врачи, услуги, пациенты, долг. Два контура: клиентский (RBAC `reports.view_own`/`view_all`) и платформенный (`is_platform_admin`, свод по всем клиникам + любая клиника). Своих таблиц нет — агрегирует приёмы и кассу | читает appointment(_tooth)/cash_payment/membership | 12 ✅ |
| `jobs` | состояние фоновых задач: журнал запусков (`job_run`, пишется воркером) и `GET /admin/jobs` + `/runs` для команды Skrezo. Сами задачи живут в `app/jobs/worker.py`, а их работа — в use-case'ах модулей | `job_run` | 14 ✅ |
| `notifications` (в `shared`) | транспорты сообщений: WhatsApp/SMS на телефон (`NOTIFY_CHANNELS`, цепочка с переходом) и Telegram Bot API в чат команды; фоновая отправка, недоставка не отменяет операцию. Отдельным модулем не стал — своих ручек и таблиц нет, а зовут его `staff`, `clinics` и `leads` | — | 14 ✅ |

## 4. Модель данных

### 4.1. Реализовано

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
  is_owner, max_discount_percent (0..100), timestamps`. Пер-клиника; базовые
  шаблоны Владелец/Менеджер/Работник. Потолок скидки — часть роли: право
  `cash.discount` разрешает операцию, потолок ограничивает её размер.
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
Клиническое ядро v2 (миграция 0019, пациент ГЛОБАЛЬНЫЙ — общий на всю платформу):
- **patient** — `id, phone(UNIQUE=дедуп платформы), first_name, last_name?,
  middle_name?, gender?, birth_date?, email?, avatar?, timestamps`. НЕ принадлежит
  клинике; клиника «видит» его при наличии приёма.
- **patient_tooth** — `id, patient_id→patient, tooth_number(FDI), status(enum
  tooth_status), note?, timestamps, UNIQUE(patient_id, tooth_number)`. Текущее
  состояние зуба (одонтограмма), одна строка на зуб. FDI: 11–48, 51–85.
- **service** — `id, clinic_id→clinic, name, description?, price(сомы),
  duration?(мин), active, timestamps`. Прайс клиники.
- **appointment** — `id, clinic_id→, patient_id→patient, doctor_membership_id?→
  membership, service_id?→service, appointment_type(enum), scheduled_at,
  duration_min, status(enum), price_total, comment?, timestamps`. Приём (врач =
  membership) занимает `[scheduled_at, scheduled_at + duration_min)`. Пересечение
  приёмов одного врача запрещено EXCLUDE-констрейнтом `ex_appointment_doctor_overlap`
  (btree_gist, мимо отменённых) — подробности в [DATABASE.md](DATABASE.md).
- **clinic_working_hours** — `id, clinic_id→clinic, weekday(0=пн…6=вс), opens_at,
  closes_at, break_start?, break_end?, is_working, timestamps,
  UNIQUE(clinic_id, weekday)`. Часы работы клиники и общий обед.
- **doctor_schedule** — `id, clinic_id→, membership_id→membership, weekday,
  starts_at, ends_at, is_working, timestamps, UNIQUE(clinic_id, membership_id,
  weekday)`. Индивидуальный график врача; **сужает** часы клиники (окно приёма =
  пересечение).
- **doctor_time_off** — `id, clinic_id→, membership_id→membership, starts_at,
  ends_at, reason?, timestamps`. Отсутствия врача (абсолютные интервалы). Пока
  только читается: ручек ведения ещё нет.
- **appointment_item** — `id, appointment_id→, service_id?→service,
  doctor_membership_id?→membership, status(enum), price_tiyin (за ЕДИНИЦУ),
  quantity (CHECK > 0), comment?, created_at`. Строка чека приёма; итог приёма =
  Σ(price_tiyin × quantity).
- **appointment_item_tooth** — `item_id→appointment_item, patient_tooth_id→
  patient_tooth` (составной PK). Связка «позиция ↔ зуб» многие-ко-многим: работа
  может покрывать несколько зубов (мост) или ни одного (чистка). История зуба =
  JOIN patient_tooth→appointment_item_tooth→appointment_item→appointment→
  clinic/doctor/service, сквозная по всем клиникам и не теряется.

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
  (allowlist/revoke). Клиники заводит только команда Skrezo.
- **Соло-врачи — OTP** (`identity`) ✅: passwordless-вход и self-service
  регистрация. `POST /auth/otp/request` → `otp/verify` (нет аккаунта →
  `registrationToken`) → `solo/register` (имя + профессия + город) создаёт
  passwordless-аккаунт, клинику `solo`, членство-владельца со специализацией и
  30-дневный триал. Повторный вход — тем же OTP. Номер с паролем (сотрудник
  клиники) по OTP не логинится. Код хранится в Redis (TTL, счётчик попыток);
  **канал доставки (SMS/WhatsApp) не выбран** — код логируется/`devCode` в dev.
  Вход пациентов по OTP — в приложении glo, вне сервиса.
- **Заявки клиник** (`clinic_applications`) ✅: второй путь после шага 2 —
  `POST /clinic-applications` (название, город, кол-во врачей; телефон из билета
  OTP, не из тела). Клиника **не создаётся автоматически**: заявка ложится в
  `clinic_application` со статусом `pending`, Skrezo смотрит её в
  `/admin/clinic-applications` (фильтр по статусу, `statusCounts`) и заводит
  клиента вручную через `POST /admin/clients`. Смена статуса пишет, кто и когда,
  и попадает в аудит.
- **Триал-функции** (`access`) ✅: клиника на триале использует только права с
  `permission.in_trial=true` (по умолчанию все); набор настраивается из
  `/admin/trial-features`. Ограничение (`403 trial_restricted`) применяют все
  guard'ы — общий `require_permission` и скоуп-guard'ы расписания/кассы/отчётов,
  включая владельца. Кеш триал-прав и статуса подписки — в Redis.
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
