"""Каталог прав и дефолтные роли клиники (единый источник истины).

Используется:
* сид-миграцией — для наполнения таблиц ``permission`` и глобальных шаблонов ``role``;
* провижинингом (фаза 3) — при создании клиники шаблоны клонируются в её роли;
* guard'ом прав (фаза 2b) — для проверки ключей.

Права **гранулярные** и настраиваются на уровне каждой клиники (пер-клиника).
Роль «Владелец» (``is_owner``) всегда имеет полный доступ и не редактируется.
"""

from __future__ import annotations

import enum
from typing import NamedTuple


class SystemRoleKey(enum.StrEnum):
    """Ключи системных (базовых) ролей клиники."""

    OWNER = "owner"  # Владелец — полный доступ, не редактируется
    MANAGER = "manager"  # Менеджер — администрирование клиники
    WORKER = "worker"  # Работник — ведёт приём, видит только своё


class PermissionDef(NamedTuple):
    key: str
    group_key: str
    description: str
    # Доступно ли право на 30-дневном триале. Сид ставит это значение по умолчанию;
    # дальше набор триал-функций настраивается из админки (таблица ``permission``).
    # По умолчанию всё включено — триал ограничивает только то, что выключит админ.
    in_trial: bool = True
    # Меняет ли право данные. Клиника с закрытой подпиской (истёкший триал,
    # просрочка, блок) переходит в режим «только чтение»: такие права ей
    # недоступны. Это свойство самой операции, а не настройка, поэтому живёт в
    # каталоге, а не колонкой в БД. ⚠️ ``billing.manage`` намеренно НЕ мутирующее:
    # им клиент и выходит из блокировки — закрыв его, мы заперли бы дверь изнутри.
    is_write: bool = False


# ── Каталог прав (расширяется по мере появления фич) ────────────────────────
PERMISSION_CATALOG: list[PermissionDef] = [
    # Расписание / Сегодня
    PermissionDef("schedule.view_all", "schedule", "Видеть расписание всех врачей"),
    PermissionDef("schedule.view_own", "schedule", "Видеть своё расписание"),
    PermissionDef(
        "schedule.manage_all", "schedule", "Управлять записями всех врачей", is_write=True
    ),
    PermissionDef("schedule.manage_own", "schedule", "Управлять своими записями", is_write=True),
    # Пациенты
    PermissionDef("patients.view", "patients", "Просмотр списка пациентов"),
    PermissionDef("patients.edit", "patients", "Редактирование пациентов", is_write=True),
    PermissionDef("patients.view_medical", "patients", "Медкарта и одонтограмма"),
    # Приём
    PermissionDef("appointments.create", "appointments", "Создание приёма", is_write=True),
    PermissionDef("appointments.edit", "appointments", "Редактирование приёма", is_write=True),
    PermissionDef(
        "appointments.complete_pay", "appointments", "Завершить и оплатить", is_write=True
    ),
    PermissionDef(
        "appointments.status_rollback",
        "appointments",
        "Откат статуса приёма назад",
        is_write=True,
    ),
    PermissionDef(
        "appointments.force_booking", "appointments", "Запись вне графика врача", is_write=True
    ),
    # Касса
    PermissionDef("cash.view_own", "cash", "Касса: свои платежи"),
    PermissionDef("cash.view_all", "cash", "Касса: платежи всей клиники"),
    PermissionDef("cash.operate", "cash", "Проведение платежей и долгов", is_write=True),
    PermissionDef(
        "cash.discount", "cash", "Скидка на приём (в пределах потолка роли)", is_write=True
    ),
    # Услуги / прайс
    PermissionDef("price.view", "price", "Просмотр прайса"),
    PermissionDef("price.edit", "price", "Редактирование прайса", is_write=True),
    # Отчёты
    PermissionDef("reports.view_own", "reports", "Отчёты по себе"),
    PermissionDef("reports.view_all", "reports", "Отчёты по всей клинике"),
    # Сотрудники
    PermissionDef("staff.view", "staff", "Просмотр сотрудников"),
    PermissionDef(
        "staff.manage", "staff", "Добавление/удаление сотрудников и ролей", is_write=True
    ),
    # Настройки клиники
    PermissionDef("settings.view", "settings", "Просмотр настроек клиники"),
    PermissionDef("settings.edit", "settings", "Редактирование настроек клиники", is_write=True),
    # Напоминания
    PermissionDef("reminders.manage", "reminders", "Управление напоминаниями", is_write=True),
    # Подписка / оплата
    PermissionDef("billing.view", "billing", "Просмотр подписки и платежей"),
    PermissionDef("billing.manage", "billing", "Оплата и привязка карты"),
    # Роли и права
    PermissionDef("roles.manage", "roles", "Управление ролями и правами", is_write=True),
    # Журнал действий
    PermissionDef("audit.view", "audit", "Журнал действий сотрудников клиники"),
    # Переписка WhatsApp (канал Cloud API)
    PermissionDef(
        "conversations.manage",
        "conversations",
        "Переписка WhatsApp с пациентами",
        is_write=True,
    ),
    # Настройки ассистента (Ф7): ступени доверия и выключатель. По умолчанию
    # только у владельца (is_owner) — решение доверить боту ответы пациентам
    # не делегируется ролью автоматически.
    PermissionDef(
        "assistant.manage",
        "settings",
        "Настройки ассистента: ступени доверия и выключатель",
        is_write=True,
    ),
]

ALL_PERMISSION_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSION_CATALOG)
#: Права, меняющие данные, — их теряет клиника с закрытой подпиской.
WRITE_PERMISSION_KEYS: frozenset[str] = frozenset(p.key for p in PERMISSION_CATALOG if p.is_write)


class RoleDef(NamedTuple):
    key: str
    name: str
    is_owner: bool
    # None → все права (владелец); иначе явный набор ключей
    permissions: tuple[str, ...] | None
    # Потолок скидки роли (%). Право ``cash.discount`` говорит «можно», это —
    # «насколько»: у владельца 100, у администратора 20, у врача 0.
    max_discount_percent: int = 0


# ── Дефолтные роли (глобальные шаблоны) ─────────────────────────────────────
DEFAULT_ROLES: list[RoleDef] = [
    RoleDef(SystemRoleKey.OWNER, "Владелец", True, None, 100),
    RoleDef(
        SystemRoleKey.MANAGER,
        "Менеджер",
        False,
        (
            "schedule.view_all",
            "schedule.manage_all",
            "patients.view",
            "patients.edit",
            "patients.view_medical",
            "appointments.create",
            "appointments.edit",
            "appointments.complete_pay",
            # Откат статуса — операция администратора: врачу не даём, чтобы
            # исправление ошибок оставалось видимым решением, а не рутиной.
            "appointments.status_rollback",
            # Запись вне графика — тоже решение администратора: врач не должен
            # сам себе ставить приёмы мимо собственных рабочих часов.
            "appointments.force_booking",
            "cash.view_all",
            "cash.operate",
            # Скидку администратор даёт, но в пределах потолка роли (20%).
            "cash.discount",
            "price.view",
            "reports.view_all",
            "staff.view",
            "staff.manage",
            "settings.view",
            "reminders.manage",
            "billing.view",
            # Журнал действий — инструмент надзора: администратор разбирает
            # спорные отмены и возвраты, поэтому право у него есть по умолчанию.
            "audit.view",
            # Переписка с пациентами — работа ресепшена (Р22: врач по умолчанию
            # пациентам не отвечает, поэтому у роли «Работник» права нет).
            "conversations.manage",
        ),
        20,
    ),
    RoleDef(
        SystemRoleKey.WORKER,
        "Работник",
        False,
        (
            "schedule.view_own",
            "schedule.manage_own",
            "patients.view",
            "patients.view_medical",
            "appointments.create",
            "appointments.edit",
            "appointments.complete_pay",
            "cash.view_own",
            "cash.operate",
            "price.view",
            "reports.view_own",
        ),
    ),
]


def resolve_permissions(role: RoleDef) -> tuple[str, ...]:
    """Вернуть эффективный набор прав роли (владелец → все)."""
    if role.is_owner or role.permissions is None:
        return ALL_PERMISSION_KEYS
    return role.permissions
