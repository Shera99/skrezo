# Схема базы данных Skrezo

Актуально на миграцию **0019** (`alembic upgrade head`). Все таблицы —
PostgreSQL, ORM в `app/shared/db/models/` (по файлу на таблицу).

## Соглашения

- **`id`** — везде `UUID v4` (PK), если не указано иное.
- **`created_at` / `updated_at`** — `timestamptz`, серверные `now()` (у большинства
  таблиц; у чисто-логовых — только `created_at`).
- **Деньги** — целые сомы (`integer`), без копеек.
- **Телефоны** — E.164 (`text`), нормализуются на входе.
- `FK` — внешний ключ, `UK` — уникальный, `PK` — первичный ключ.
- Каскады: `ON DELETE CASCADE` (удаление родителя удаляет детей) или `SET NULL`
  (обнуляет ссылку) — указаны в разделе связей.

## Домены

| Домен | Таблицы |
|---|---|
| Справочники (i18n) | `city`, `city_translation`, `specialization`, `specialization_translation` |
| Аккаунты и клиники | `account`, `clinic`, `membership` |
| RBAC (права/роли) | `permission`, `role`, `role_permission` |
| Продажи / биллинг | `lead`, `subscription`, `subscription_event`, `tariff_version`, `tariff_package` |
| Аудит | `audit_log` |
| **Клиническое ядро** | `patient`, `patient_tooth`, `service`, `appointment`, `appointment_tooth` |

---

## ER-диаграмма

```mermaid
erDiagram
    CITY ||--o{ CITY_TRANSLATION : "переводы"
    SPECIALIZATION ||--o{ SPECIALIZATION_TRANSLATION : "переводы"
    CITY ||--o{ CLINIC : "город"

    CLINIC ||--o{ MEMBERSHIP : "сотрудники"
    ACCOUNT ||--o{ MEMBERSHIP : "членства"
    ROLE ||--o{ MEMBERSHIP : "роль"
    SPECIALIZATION ||--o{ MEMBERSHIP : "спец. (опц.)"

    CLINIC ||--o{ ROLE : "роли клиники (NULL=шаблон)"
    ROLE ||--o{ ROLE_PERMISSION : ""
    PERMISSION ||--o{ ROLE_PERMISSION : ""

    CLINIC ||--o| SUBSCRIPTION : "подписка (1:1)"
    SUBSCRIPTION ||--o{ SUBSCRIPTION_EVENT : "история статусов"
    CLINIC ||--o{ LEAD : "конвертация (опц.)"

    ACCOUNT ||--o{ TARIFF_VERSION : "created_by"
    TARIFF_VERSION ||--o{ TARIFF_PACKAGE : "пакеты (clinic)"
    ACCOUNT ||--o{ AUDIT_LOG : "actor"

    PATIENT ||--o{ PATIENT_TOOTH : "одонтограмма"
    CLINIC ||--o{ SERVICE : "прайс"
    CLINIC ||--o{ APPOINTMENT : "приёмы"
    PATIENT ||--o{ APPOINTMENT : "пациент (глобальный)"
    MEMBERSHIP ||--o{ APPOINTMENT : "врач (опц.)"
    APPOINTMENT ||--o{ APPOINTMENT_TOOTH : "манипуляции"
    PATIENT_TOOTH ||--o{ APPOINTMENT_TOOTH : "зуб"
    SERVICE ||--o{ APPOINTMENT_TOOTH : "услуга (опц.)"
    MEMBERSHIP ||--o{ APPOINTMENT_TOOTH : "врач (опц.)"

    ACCOUNT {
        uuid id PK
        text phone UK
        text full_name
        enum lang
        bool onboarded
        text password_hash "nullable"
        bool must_change_password
        bool is_platform_admin "команда Skrezo"
    }
    CLINIC {
        uuid id PK
        text name
        enum type "solo|clinic"
        uuid city_id FK "nullable"
        text address "nullable"
        text business_phone "nullable"
        enum lang
        int seats_limit "nullable"
        text support_notes "nullable"
    }
    MEMBERSHIP {
        uuid id PK
        uuid clinic_id FK
        uuid account_id FK
        uuid role_id FK
        uuid specialization_id FK "nullable"
        bool active
    }
    ROLE {
        uuid id PK
        uuid clinic_id FK "NULL=глобальный шаблон"
        text key "nullable"
        text name
        bool is_system
        bool is_owner
    }
    PERMISSION {
        uuid id PK
        text key UK
        text group_key
        text description
        int sort_order
    }
    ROLE_PERMISSION {
        uuid role_id FK "составной PK"
        uuid permission_id FK "составной PK"
    }
    CITY {
        uuid id PK
        int sort_order
        bool active
    }
    CITY_TRANSLATION {
        uuid id PK
        uuid city_id FK
        enum lang
        text value
    }
    SPECIALIZATION {
        uuid id PK
        int sort_order
        bool active
    }
    SPECIALIZATION_TRANSLATION {
        uuid id PK
        uuid specialization_id FK
        enum lang
        text value
    }
    LEAD {
        uuid id PK
        text full_name
        text phone UK
        text clinic_name
        text doctors_count_range "nullable"
        text comment "nullable"
        uuid converted_clinic_id FK "nullable"
        timestamptz created_at
    }
    SUBSCRIPTION {
        uuid id PK
        uuid clinic_id FK "UK: 1 на клинику"
        enum status
        timestamptz trial_started_at
        timestamptz trial_ends_at
        timestamptz card_attach_deadline "nullable"
        timestamptz card_attached_at "nullable"
        timestamptz current_period_start "nullable"
        timestamptz current_period_end "nullable"
        timestamptz cancelled_at "nullable"
    }
    SUBSCRIPTION_EVENT {
        uuid id PK
        uuid subscription_id FK
        enum from_status "nullable"
        enum to_status
        text reason "nullable"
        timestamptz created_at
    }
    TARIFF_VERSION {
        uuid id PK
        enum type "solo|clinic"
        int version "по типу, от 1"
        int price "nullable (solo)"
        int over_price "nullable (clinic Сеть)"
        uuid created_by FK
        timestamptz created_at
    }
    TARIFF_PACKAGE {
        uuid id PK
        uuid tariff_version_id FK
        text name
        int up_to "граница мест"
        int price
    }
    AUDIT_LOG {
        uuid id PK
        uuid actor_id FK "account"
        text action
        text entity_type
        uuid entity_id "nullable"
        jsonb payload "nullable"
        timestamptz created_at
    }
    PATIENT {
        uuid id PK
        text phone UK "глобальный дедуп"
        text first_name
        text last_name "nullable"
        text middle_name "nullable"
        text gender "nullable"
        date birth_date "nullable"
        text email "nullable"
        text avatar "nullable"
    }
    PATIENT_TOOTH {
        uuid id PK
        uuid patient_id FK
        smallint tooth_number "FDI"
        enum status "tooth_status"
        text note "nullable"
    }
    SERVICE {
        uuid id PK
        uuid clinic_id FK
        text name
        text description "nullable"
        int price "сомы"
        int duration "nullable, мин"
        bool active
    }
    APPOINTMENT {
        uuid id PK
        uuid clinic_id FK
        uuid patient_id FK
        uuid doctor_membership_id FK "nullable"
        enum appointment_type
        timestamptz scheduled_at
        enum status
        int price_total
        text comment "nullable"
    }
    APPOINTMENT_TOOTH {
        uuid id PK
        uuid appointment_id FK
        uuid patient_tooth_id FK
        uuid service_id FK "nullable"
        uuid doctor_membership_id FK "nullable"
        enum status "appointment_tooth_status"
        int price
        text comment "nullable"
        timestamptz created_at
    }
```

> `UNIQUE(patient_id, tooth_number)` на `patient_tooth`; `UNIQUE(<entity>_id, lang)`
> на таблицах переводов.

---

## Перечисления (enum)

| Enum (PG-тип) | Значения | Где |
|---|---|---|
| `lang_enum` | `kg`, `kz`, `ru`, `en` | account/clinic/переводы |
| `clinic_type` | `solo`, `clinic` | clinic, tariff_version |
| `subscription_status` | `trial`, `awaiting_card`, `active`, `past_due`, `blocked`, `cancelled` | subscription(_event) |
| `tooth_status` | `healthy`, `caries`, `filling`, `crown`, `implant`, `missing`, `broken`, `root`, `under_treatment` | patient_tooth |
| `appointment_type` | `consultation`, `treatment`, `operation`, `cleaning`, `xray` | appointment |
| `appointment_status` | `scheduled`, `confirmed`, `in_progress`, `completed`, `cancelled`, `no_show` | appointment |
| `appointment_tooth_status` | `planned`, `started`, `completed`, `cancelled` | appointment_tooth |

---

## Клиническое ядро — как читать историю зуба

Пациент **глобальный** (не принадлежит клинике). Клиника «видит» пациента, если у
неё есть с ним `appointment`. **Сквозная история зуба** (по всем клиникам):

```
patient ──1:N── patient_tooth ──1:N── appointment_tooth ──N:1── appointment ──N:1── clinic
                     (текущее                (манипуляция)          (приём)        + doctor(membership)
                      состояние зуба)                                              + service
```

- **Одонтограмма** = строки `patient_tooth` (по одной на зуб, `UNIQUE`).
- **История зуба** = все `appointment_tooth` для этого `patient_tooth`, JOIN к
  `appointment` (дата, клиника), `membership→account` (врач), `service` (услуга).
- **Врач** — это `membership` (account + роль в клинике), отдельной таблицы
  `doctors` нет.

---

## Каскады удаления (ON DELETE)

| Дочерняя таблица | Ссылка | Поведение |
|---|---|---|
| `membership` | clinic_id, account_id | CASCADE |
| `role` | clinic_id | CASCADE |
| `role_permission` | role_id, permission_id | CASCADE |
| `*_translation` | <entity>_id | CASCADE |
| `subscription` / `subscription_event` | clinic_id / subscription_id | CASCADE |
| `tariff_package` | tariff_version_id | CASCADE |
| `patient_tooth` | patient_id | CASCADE |
| `service` | clinic_id | CASCADE |
| `appointment` | clinic_id, patient_id | CASCADE |
| `appointment` | doctor_membership_id | SET NULL |
| `appointment_tooth` | appointment_id, patient_tooth_id | CASCADE |
| `appointment_tooth` | service_id, doctor_membership_id | SET NULL |
| `clinic` | city_id | (без каскада) |
| `tariff_version` / `audit_log` | created_by / actor_id → account | (без каскада) |
| `lead` | converted_clinic_id | (без каскада, nullable) |
