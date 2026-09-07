"""Все ORM-модели проекта (по файлу на таблицу).

Импорт пакета регистрирует все таблицы в ``Base.metadata`` (нужно для Alembic
и тестов) и даёт удобный доступ: ``from app.shared.db.models import Account``.
"""

from __future__ import annotations

from app.shared.db.models.account import Account
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_knowledge_gap import AiKnowledgeGap
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_regression_case import AiRegressionCase
from app.shared.db.models.ai_regression_result import AiRegressionResult
from app.shared.db.models.ai_regression_run import AiRegressionRun
from app.shared.db.models.ai_summary import AiSummary
from app.shared.db.models.ai_tool_call import AiToolCall
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_item import AppointmentItem
from app.shared.db.models.appointment_item_tooth import AppointmentItemTooth
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.city import City
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_application import ClinicApplication
from app.shared.db.models.clinic_usage_day import ClinicUsageDay
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.clinic_working_hours import ClinicWorkingHours
from app.shared.db.models.device import Device
from app.shared.db.models.doctor_schedule import DoctorSchedule
from app.shared.db.models.doctor_time_off import DoctorTimeOff
from app.shared.db.models.event import Event
from app.shared.db.models.glossary_term import GlossaryTerm
from app.shared.db.models.inbox_item import InboxItem
from app.shared.db.models.job_run import JobRun
from app.shared.db.models.kb_document import KbDocument
from app.shared.db.models.kb_document_version import KbDocumentVersion
from app.shared.db.models.lead import Lead
from app.shared.db.models.membership import Membership
from app.shared.db.models.notification_delivery import NotificationDelivery
from app.shared.db.models.notification_pref import NotificationPref
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_file import PatientFile
from app.shared.db.models.patient_tooth import PatientTooth
from app.shared.db.models.permission import Permission
from app.shared.db.models.push_event import PushEvent
from app.shared.db.models.push_job import PushJob
from app.shared.db.models.role import Role
from app.shared.db.models.role_permission import RolePermission
from app.shared.db.models.service import Service
from app.shared.db.models.service_doctor import ServiceDoctor
from app.shared.db.models.slot_hold import SlotHold
from app.shared.db.models.specialization import Specialization
from app.shared.db.models.specialization_translation import SpecializationTranslation
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.subscription_event import SubscriptionEvent
from app.shared.db.models.tariff_package import TariffPackage
from app.shared.db.models.tariff_version import TariffVersion
from app.shared.db.models.whatsapp_template import WhatsAppTemplate

__all__ = [
    "Account",
    "AiConversation",
    "AiEscalation",
    "AiKnowledgeGap",
    "AiMessage",
    "AiRegressionCase",
    "AiRegressionResult",
    "AiRegressionRun",
    "AiSummary",
    "AiToolCall",
    "Appointment",
    "AppointmentItem",
    "AppointmentItemTooth",
    "AuditLog",
    "CashPayment",
    "City",
    "CityTranslation",
    "Clinic",
    "ClinicApplication",
    "ClinicUsageDay",
    "ClinicWhatsApp",
    "ClinicWorkingHours",
    "Device",
    "DoctorSchedule",
    "DoctorTimeOff",
    "Event",
    "GlossaryTerm",
    "InboxItem",
    "JobRun",
    "KbDocument",
    "KbDocumentVersion",
    "Lead",
    "Membership",
    "NotificationDelivery",
    "NotificationPref",
    "Patient",
    "PatientFile",
    "PatientTooth",
    "Permission",
    "PushEvent",
    "PushJob",
    "Role",
    "RolePermission",
    "Service",
    "ServiceDoctor",
    "SlotHold",
    "Specialization",
    "SpecializationTranslation",
    "Subscription",
    "SubscriptionEvent",
    "TariffPackage",
    "TariffVersion",
    "WhatsAppTemplate",
]
