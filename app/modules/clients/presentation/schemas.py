"""Схемы раздела «Клиенты» (контракты фронта админ-панели, 1:1).

`patientsCount`, `revenueTiyin` и блок `activity` считаются из клинического ядра
и кассы клиники. ⚠️ `revenueTiyin` — оборот САМОЙ клиники, а не деньги, которые
она принесла Skrezo: последнее — это `lifetimeRevenueTiyin`, и оно ждёт биллинга.

Остаются заглушками (биллинг отложен): `lifetimeRevenue`/`lastActiveAt` = null,
`payments` = [], `cardAttached` = false (карты не привязываются). `tariffLabel`
пока = `tariffName` (пер-клиничный тариф не назначается), `activity.lastLogin`
пуст — входы не отслеживаются.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import ConfigDict, Field

from app.modules.clients.domain.models import ClientDetail, ClientListRow, StaffRow
from app.modules.clients.infrastructure.repositories.client_repository import tariff_name_for
from app.shared.api.schemas import CamelModel, NotNullFieldsModel
from app.shared.db.enums import AiMode, ClinicType, Lang, SubscriptionStatus, WhatsAppMode
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp

_TRIAL_DAYS = 30


def _trial_day(status: SubscriptionStatus | None, trial_started_at: datetime | None) -> int | None:
    """Номер дня триала 1..30 (только для status=trial), иначе None."""
    if status is not SubscriptionStatus.TRIAL or trial_started_at is None:
        return None
    elapsed = (datetime.now(UTC) - trial_started_at).days + 1
    return max(1, min(_TRIAL_DAYS, elapsed))


class ClientListItem(CamelModel):
    id: uuid.UUID
    type: ClinicType
    name: str
    admin_full_name: str | None = None
    admin_phone: str | None = None
    admin_blocked: bool = Field(
        default=False, description="Аккаунт владельца заблокирован — он не войдёт в кабинет"
    )
    city_name: str | None = None
    tariff_name: str = Field(description="Коарс-ярлык тарифа для фильтра: «Соло» | «Клиника»")
    tariff_label: str = Field(description="Человекочитаемый ярлык тарифа")
    seats_used: int
    seats_limit: int | None = None
    patients_count: int = Field(
        default=0, description="Пациентов клиники — тех, кого она принимала (пациент глобальный)"
    )
    revenue_tiyin: int = Field(
        default=0, description="Нетто кассы КЛИНИКИ за всё время: платежи минус возвраты"
    )
    lifetime_revenue_tiyin: int | None = Field(
        default=None,
        description=(
            "Сколько клиент заплатил SKREZO за всё время — ждёт биллинга "
            "(не путать с `revenueTiyin`: то оборот самой клиники)"
        ),
    )
    subscription_status: SubscriptionStatus | None = None
    trial_day: int | None = None
    last_active_at: datetime | None = None
    created_at: datetime
    current_period_end: datetime | None = None

    @classmethod
    def from_row(cls, row: ClientListRow) -> ClientListItem:
        name = tariff_name_for(row.type)
        return cls(
            id=row.id,
            type=row.type,
            name=row.name,
            admin_full_name=row.admin_full_name,
            admin_phone=row.admin_phone,
            admin_blocked=row.admin_blocked_at is not None,
            city_name=row.city_name,
            tariff_name=name,
            tariff_label=name,
            seats_used=row.seats_used,
            seats_limit=row.seats_limit,
            subscription_status=row.subscription_status,
            trial_day=_trial_day(row.subscription_status, row.trial_started_at),
            created_at=row.created_at,
            current_period_end=row.current_period_end,
            patients_count=row.patients_count,
            revenue_tiyin=row.revenue_tiyin,
        )


class ClientsResponse(CamelModel):
    list: list[ClientListItem]
    current_page: int
    page_size: int = 25
    total_count: int


class StatusCountsResponse(CamelModel):
    """Число клиентов в каждом статусе подписки (для дропдауна фильтра)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "trial": 12,
                "trialExpired": 3,
                "awaitingCard": 0,
                "active": 41,
                "pastDue": 2,
                "blocked": 1,
                "cancelled": 5,
            }
        }
    )

    trial: int = Field(default=0, description="Триал идёт")
    trial_expired: int = Field(
        default=0, description="Триал кончился, оплаты не было — клиника в режиме чтения"
    )
    awaiting_card: int = 0
    active: int = 0
    past_due: int = 0
    blocked: int = 0
    cancelled: int = 0


class PhoneCheckResponse(CamelModel):
    owner_name: str | None = Field(default=None, description="ФИО владельца номера или null")


class StaffMember(CamelModel):
    """Сотрудник клиники в карточке клиента.

    Исключённые из списка не пропадают: на их членстве висят приёмы и платежи, и
    карточка должна объяснять, кто их вёл. Отличает их флаг `active` — без него
    уволенный врач выглядел бы в админке точно так же, как работающий.
    """

    id: uuid.UUID = Field(description="Членство в клинике (membershipId)")
    account_id: uuid.UUID = Field(
        description=(
            "Аккаунт человека — для `POST /admin/accounts/{id}/block`: блокировка идёт по "
            "аккаунту (сразу во всех клиниках), а `id` здесь — членство в этой клинике"
        ),
        examples=["5d2e8f1a-3b4c-4d5e-8f6a-7b8c9d0e1f2a"],
    )
    full_name: str
    phone: str
    role: str = Field(description="admin (владелец) | doctor (врач)")
    specialization: str | None = None
    active: bool = Field(
        description=(
            "false — сотрудник исключён из клиники: доступа к ней нет "
            "(403 `membership_revoked`), место в тарифе не занимает, "
            "история приёмов сохранена"
        ),
        examples=[True],
    )
    blocked: bool = Field(
        default=False,
        description=(
            "Аккаунт заблокирован (`POST /admin/accounts/{id}/block`): человек не "
            "войдёт вовсе, ни в одну клинику. Не путать с `active` — тот про "
            "членство именно в этой клинике"
        ),
    )

    @classmethod
    def from_row(cls, staff: StaffRow) -> StaffMember:
        return cls(
            id=staff.membership_id,
            account_id=staff.account_id,
            full_name=staff.full_name,
            phone=staff.phone,
            role="admin" if staff.is_owner else "doctor",
            specialization=staff.specialization_name,
            active=staff.active,
            blocked=staff.blocked_at is not None,
        )


class ClientPayment(CamelModel):
    id: str
    label: str
    date: datetime
    amount_tiyin: int
    status: str = Field(description="success | failed | refunded")


class ClientActivity(CamelModel):
    """Активность клиники: сколько людей она принимала и сколько раз."""

    patients: int = Field(description="Пациентов, которых принимала клиника")
    visits: int = Field(description="Приёмов за всё время (любого статуса)")
    last_login: str = Field(
        default="", description="Последний вход — не отслеживается, всегда пусто"
    )


class ClientWhatsApp(CamelModel):
    """Привязка номера WhatsApp Cloud API к клинике — то, что стоит сейчас."""

    phone_number_id: str = Field(
        description="phone_number_id номера в Meta — по нему вебхук находит клинику",
        examples=["1324175910774071"],
    )
    display_phone: str = Field(
        description="Номер, как его видят пациенты", examples=["+996700999000"]
    )
    waba_id: str | None = Field(
        default=None,
        description="WhatsApp Business Account — нужен для сверки шаблонов",
        examples=["1101036182272785"],
    )
    mode: WhatsAppMode = Field(
        description="api_only — отдельный номер; coexistence — рабочий номер клиники + приложение"
    )
    active: bool = Field(description="false — привязка выключена, входящие не принимаются")

    @classmethod
    def from_binding(cls, binding: ClinicWhatsApp) -> ClientWhatsApp:
        return cls(
            phone_number_id=binding.phone_number_id,
            display_phone=binding.display_phone,
            waba_id=binding.waba_id,
            mode=binding.mode,
            active=binding.active,
        )


class ClientCustomPlan(CamelModel):
    """Спецплан клиента (`PUT /admin/clients/{id}/custom-plan`)."""

    title: str = Field(examples=["Клиника+ 14 мест"])
    seats: int = Field(examples=[14])
    price_tiyin: int = Field(description="Цена в месяц, тыйыны", examples=[1100000])
    note: str | None = Field(default=None, description="Внутренняя заметка Skrezo")
    updated_at: datetime


class ClientPlanRequest(CamelModel):
    """Открытая заявка клиента на условия сверх тарифов."""

    id: uuid.UUID
    seats: int = Field(description="Сколько мест хочет", examples=[14])
    comment: str | None = None
    submitted_at: datetime


class ClientDetailResponse(ClientListItem):
    address: str | None = None
    business_phone: str | None = None
    language: str
    card_attached: bool = False
    specialization: str | None = None
    activity: ClientActivity | None = None
    staff: list[StaffMember]
    payments: list[ClientPayment] = Field(default_factory=list)
    notes: str = ""
    whatsapp: ClientWhatsApp | None = Field(
        default=None,
        description=(
            "Текущая привязка WhatsApp (`PUT /admin/clients/{id}/whatsapp`); `null` — номер "
            "не подключён"
        ),
    )
    ai_mode: AiMode = Field(
        description=(
            "Режим ассистента (`PUT /admin/clients/{id}/ai-mode`): off — выключен, shadow — "
            "«Наблюдаю», assisted — «Проверяю», auto — «Доверяю»"
        ),
        examples=["shadow"],
    )
    ai_mode_reason: str | None = Field(
        default=None,
        description="Почему режим такой: кто и зачем сменил (например, мониторинг опустил)",
    )
    ai_mode_changed_at: datetime | None = Field(
        default=None, description="Когда режим меняли в последний раз"
    )
    custom_plan: ClientCustomPlan | None = Field(
        default=None,
        description="Спецплан: свои места и цена вместо пакетов; `null` — публичные пакеты",
    )
    open_plan_request: ClientPlanRequest | None = Field(
        default=None,
        description="Открытая заявка клиента «нужно больше мест» (`/admin/plan-requests`)",
    )

    @classmethod
    def from_detail(cls, detail: ClientDetail) -> ClientDetailResponse:
        clinic = detail.clinic
        sub = detail.subscription
        owner = next((s for s in detail.staff if s.is_owner), None)
        name = tariff_name_for(clinic.type)
        return cls(
            id=clinic.id,
            type=clinic.type,
            name=clinic.name,
            admin_full_name=owner.full_name if owner else None,
            admin_phone=owner.phone if owner else None,
            admin_blocked=bool(owner and owner.blocked_at is not None),
            city_name=detail.city_name,
            tariff_name=name,
            tariff_label=name,
            seats_used=detail.seats_used,
            seats_limit=clinic.seats_limit,
            patients_count=detail.patients_count,
            revenue_tiyin=detail.revenue_tiyin,
            subscription_status=sub.status if sub else None,
            trial_day=_trial_day(
                sub.status if sub else None, sub.trial_started_at if sub else None
            ),
            created_at=clinic.created_at,
            current_period_end=sub.current_period_end if sub else None,
            address=clinic.address,
            business_phone=clinic.business_phone,
            language=clinic.lang.value,
            card_attached=bool(sub and sub.card_attached_at is not None),
            specialization=owner.specialization_name if owner else None,
            activity=ClientActivity(
                patients=detail.patients_count, visits=detail.visits_count
            ),
            staff=[StaffMember.from_row(s) for s in detail.staff],
            payments=[],  # журнал платежей клиента → Skrezo: биллинг отложен
            notes=clinic.support_notes or "",
            whatsapp=ClientWhatsApp.from_binding(detail.whatsapp) if detail.whatsapp else None,
            ai_mode=clinic.ai_mode,
            ai_mode_reason=clinic.ai_mode_reason,
            ai_mode_changed_at=clinic.ai_mode_changed_at,
            custom_plan=(
                ClientCustomPlan(
                    title=detail.custom_plan.title,
                    seats=detail.custom_plan.seats,
                    price_tiyin=detail.custom_plan.price_tiyin,
                    note=detail.custom_plan.note,
                    updated_at=detail.custom_plan.updated_at,
                )
                if detail.custom_plan is not None
                else None
            ),
            open_plan_request=(
                ClientPlanRequest(
                    id=detail.open_plan_request.id,
                    seats=detail.open_plan_request.seats,
                    comment=detail.open_plan_request.comment,
                    submitted_at=detail.open_plan_request.submitted_at,
                )
                if detail.open_plan_request is not None
                else None
            ),
        )


class CreateAccountRequest(CamelModel):
    type: ClinicType = Field(description="Тип аккаунта: solo|clinic")
    admin_full_name: str = Field(min_length=2, description="ФИО администратора/врача")
    admin_phone: str = Field(description="Телефон администратора (= логин)")
    seats: int | None = Field(default=None, ge=2, description="Мест (клиника; минимум 2)")
    city_id: uuid.UUID | None = Field(default=None, description="Город (обяз. для clinic)")
    name: str | None = Field(default=None, min_length=2, description="Название (обяз. для clinic)")
    address: str | None = None
    business_phone: str | None = Field(default=None, description="Телефон бизнеса (клиника)")
    specialization_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Специализация владельца. Задана — владелец сам принимает пациентов: "
            "у него появляется колонка в ленте дня и на него можно записать. "
            "Пусто — владелец только управляет клиникой. Для соло обязательна по "
            "смыслу: без неё врач не сможет вести приём"
        ),
    )
    application_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Заявка из очереди `/admin/clinic-applications`, из которой заводится "
            "клиент; помечается сконвертированной и рассмотренной"
        ),
        examples=["3f6c1b2a-8d4e-4f5a-9b7c-1e2d3c4b5a60"],
    )
    language: Lang = Lang.RU


class AccountCreatedResponse(CamelModel):
    """Клиент заведён. Пароля нет: владелец входит по коду на свой номер."""

    id: uuid.UUID
    type: ClinicType
    admin_phone: str = Field(description="Телефон владельца (= логин, вход по коду из SMS)")


class UpdateClientRequest(NotNullFieldsModel):
    """Правка карточки клиента: передаются только изменяемые поля.

    ``null`` осмыслен лишь для NULLABLE-колонок (снять адрес, город, заметки).
    Для полей, которые в БД NOT NULL, ``null`` — ошибка валидации (422): имена
    здесь ключей запроса (``name``, ``language`` → ``clinic.lang``).
    """

    NOT_NULL_FIELDS = ("name", "language")

    name: str | None = Field(default=None, min_length=2)
    address: str | None = None
    city_id: uuid.UUID | None = None
    business_phone: str | None = None
    language: Lang | None = None
    notes: str | None = None
    admin_full_name: str | None = Field(default=None, min_length=2, description="ФИО владельца")
    specialization_id: uuid.UUID | None = Field(default=None, description="Специализация (соло)")


class ChangeAdminPhoneRequest(CamelModel):
    new_phone: str = Field(description="Новый номер администратора (= логин владельца)")


class ArchiveClientRequest(CamelModel):
    reason: str | None = Field(default=None, description="Причина архивации")


class ChangeAdminPhoneResponse(CamelModel):
    ok: bool = True
    phone: str = Field(description="Установленный номер администратора (E.164)")


class DoctorCreateRequest(CamelModel):
    full_name: str = Field(min_length=2, description="ФИО врача")
    phone: str = Field(description="Телефон (= логин)")
    specialization_id: uuid.UUID = Field(description="Специализация (обязательна)")


class DoctorUpdateRequest(CamelModel):
    full_name: str | None = Field(default=None, min_length=2)
    phone: str | None = None
    specialization_id: uuid.UUID | None = None


class DoctorCreatedResponse(CamelModel):
    """Врач заведён. Пароля нет: он входит по коду на свой номер."""

    id: uuid.UUID = Field(description="membershipId созданного врача")


class DoctorRemovedResponse(CamelModel):
    """Врач исключён из клиники: доступ к ней закрыт, аккаунт сохранён."""

    ok: bool = True
    mode: str = Field(
        description="deactivated (soft) — членство деактивировано, история приёмов сохранена",
        examples=["deactivated"],
    )
    access_revoked: bool = Field(
        default=True,
        description=(
            "Доступ к этой клинике закрыт сразу: выданные токены перестают "
            "работать на её эндпоинтах (403 `membership_revoked`)"
        ),
    )
    account_kept: bool = Field(
        default=True,
        description=(
            "Аккаунт врача не удалён — он глобальный и может работать в других "
            "клиниках; вход по коду остаётся, но без доступа к этой клинике"
        ),
    )
