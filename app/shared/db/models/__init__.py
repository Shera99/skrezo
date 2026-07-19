"""Все ORM-модели проекта (по файлу на таблицу).

Импорт пакета регистрирует все таблицы в ``Base.metadata`` (нужно для Alembic
и тестов) и даёт удобный доступ: ``from app.shared.db.models import Account``.
"""

from __future__ import annotations

from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_tooth import AppointmentTooth
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.city import City
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_working_hours import ClinicWorkingHours
from app.shared.db.models.doctor_schedule import DoctorSchedule
from app.shared.db.models.doctor_time_off import DoctorTimeOff
from app.shared.db.models.lead import Lead
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_tooth import PatientTooth
from app.shared.db.models.permission import Permission
from app.shared.db.models.role import Role
from app.shared.db.models.role_permission import RolePermission
from app.shared.db.models.service import Service
from app.shared.db.models.specialization import Specialization
from app.shared.db.models.specialization_translation import SpecializationTranslation
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.subscription_event import SubscriptionEvent
from app.shared.db.models.tariff_package import TariffPackage
from app.shared.db.models.tariff_version import TariffVersion

__all__ = [
    "Account",
    "Appointment",
    "AppointmentTooth",
    "AuditLog",
    "CashPayment",
    "City",
    "CityTranslation",
    "Clinic",
    "ClinicWorkingHours",
    "DoctorSchedule",
    "DoctorTimeOff",
    "Lead",
    "Membership",
    "Patient",
    "PatientTooth",
    "Permission",
    "Role",
    "RolePermission",
    "Service",
    "Specialization",
    "SpecializationTranslation",
    "Subscription",
    "SubscriptionEvent",
    "TariffPackage",
    "TariffVersion",
]
