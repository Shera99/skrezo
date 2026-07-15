"""Все ORM-модели проекта (по файлу на таблицу).

Импорт пакета регистрирует все таблицы в ``Base.metadata`` (нужно для Alembic
и тестов) и даёт удобный доступ: ``from app.shared.db.models import Account``.
"""

from __future__ import annotations

from app.shared.db.models.account import Account
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.city import City
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.lead import Lead
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.permission import Permission
from app.shared.db.models.role import Role
from app.shared.db.models.role_permission import RolePermission
from app.shared.db.models.specialization import Specialization
from app.shared.db.models.specialization_translation import SpecializationTranslation
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.subscription_event import SubscriptionEvent
from app.shared.db.models.tariff_package import TariffPackage
from app.shared.db.models.tariff_version import TariffVersion
from app.shared.db.models.tooth_record import ToothRecord
from app.shared.db.models.visit import Visit

__all__ = [
    "Account",
    "AuditLog",
    "City",
    "CityTranslation",
    "Clinic",
    "Lead",
    "Membership",
    "Patient",
    "Permission",
    "Role",
    "RolePermission",
    "Specialization",
    "SpecializationTranslation",
    "Subscription",
    "SubscriptionEvent",
    "TariffPackage",
    "TariffVersion",
    "ToothRecord",
    "Visit",
]
