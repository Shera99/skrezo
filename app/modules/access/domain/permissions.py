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


# ── Каталог прав (расширяется по мере появления фич) ────────────────────────
PERMISSION_CATALOG: list[PermissionDef] = [
    # Расписание / Сегодня
    PermissionDef("schedule.view_all", "schedule", "Видеть расписание всех врачей"),
    PermissionDef("schedule.view_own", "schedule", "Видеть своё расписание"),
    PermissionDef("schedule.manage_all", "schedule", "Управлять записями всех врачей"),
    PermissionDef("schedule.manage_own", "schedule", "Управлять своими записями"),
    # Пациенты
    PermissionDef("patients.view", "patients", "Просмотр списка пациентов"),
    PermissionDef("patients.edit", "patients", "Редактирование пациентов"),
    PermissionDef("patients.view_medical", "patients", "Медкарта и одонтограмма"),
    # Приём
    PermissionDef("appointments.create", "appointments", "Создание приёма"),
    PermissionDef("appointments.edit", "appointments", "Редактирование приёма"),
    PermissionDef("appointments.complete_pay", "appointments", "Завершить и оплатить"),
    # Касса
    PermissionDef("cash.view_own", "cash", "Касса: свои платежи"),
    PermissionDef("cash.view_all", "cash", "Касса: платежи всей клиники"),
    PermissionDef("cash.operate", "cash", "Проведение платежей и долгов"),
    # Услуги / прайс
    PermissionDef("price.view", "price", "Просмотр прайса"),
    PermissionDef("price.edit", "price", "Редактирование прайса"),
    # Отчёты
    PermissionDef("reports.view_own", "reports", "Отчёты по себе"),
    PermissionDef("reports.view_all", "reports", "Отчёты по всей клинике"),
    # Сотрудники
    PermissionDef("staff.view", "staff", "Просмотр сотрудников"),
    PermissionDef("staff.manage", "staff", "Добавление/удаление сотрудников и ролей"),
    # Настройки клиники
    PermissionDef("settings.view", "settings", "Просмотр настроек клиники"),
    PermissionDef("settings.edit", "settings", "Редактирование настроек клиники"),
    # Напоминания
    PermissionDef("reminders.manage", "reminders", "Управление напоминаниями"),
    # Подписка / оплата
    PermissionDef("billing.view", "billing", "Просмотр подписки и платежей"),
    PermissionDef("billing.manage", "billing", "Оплата и привязка карты"),
    # Роли и права
    PermissionDef("roles.manage", "roles", "Управление ролями и правами"),
]

ALL_PERMISSION_KEYS: tuple[str, ...] = tuple(p.key for p in PERMISSION_CATALOG)


class RoleDef(NamedTuple):
    key: str
    name: str
    is_owner: bool
    # None → все права (владелец); иначе явный набор ключей
    permissions: tuple[str, ...] | None


# ── Дефолтные роли (глобальные шаблоны) ─────────────────────────────────────
DEFAULT_ROLES: list[RoleDef] = [
    RoleDef(SystemRoleKey.OWNER, "Владелец", True, None),
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
            "cash.view_all",
            "cash.operate",
            "price.view",
            "reports.view_all",
            "staff.view",
            "staff.manage",
            "settings.view",
            "reminders.manage",
            "billing.view",
        ),
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
