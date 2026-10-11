"""Карточки клиента для чата команды Skrezo: новая регистрация и конец триала.

Карточка отвечает на три вопроса продаж: кто это (клиника или соло-врач, город,
специализация), насколько он живой (сотрудники, пациенты, приёмы) и как с ним
связаться (телефоны в тексте, кнопки «написать» под сообщением).

Текст размечен HTML; всё введённое людьми экранируется в
:mod:`app.shared.notifications.team_format`. Пустые поля в карточку не
попадают — иначе в канале были бы строки-прочерки.
"""

from __future__ import annotations

from app.modules.clients.domain.models import ClientCard
from app.shared.db.enums import ClinicType
from app.shared.notifications.channels import ChatButton
from app.shared.notifications.team_format import (
    contact_buttons,
    esc,
    field,
    plural,
    team_date,
    team_datetime,
)


def _kind_tag(card: ClientCard) -> str:
    return "#соло" if card.type is ClinicType.SOLO else "#клиника"


def _who(card: ClientCard) -> list[str]:
    """Кто это: клиника с владельцем или соло-врач (у соло клиника = сам врач)."""
    lines: list[str] = []
    if card.type is ClinicType.CLINIC:
        lines.append(field("🏥", "Клиника", card.name))
        if card.owner_full_name:
            lines.append(field("👤", "Владелец", card.owner_full_name))
    else:
        lines.append(field("👤", "Соло-врач", card.owner_full_name or card.name))
    if card.owner_specialization:
        lines.append(field("🩺", "Специализация", card.owner_specialization))
    return lines


def _contacts(card: ClientCard) -> list[str]:
    """Как связаться: телефон владельца, телефон клиники (если другой), адрес."""
    lines: list[str] = []
    if card.owner_phone:
        lines.append(field("📞", "Телефон", card.owner_phone))
    if card.business_phone and card.business_phone != card.owner_phone:
        lines.append(field("☎️", "Телефон клиники", card.business_phone))
    place = ", ".join(part for part in (card.city_name, card.address) if part)
    if place:
        lines.append(field("📍", "Адрес", place))
    return lines


def _activity(card: ClientCard) -> str:
    """Насколько клиент живой. Ноль приёмов — отдельной фразой: это главный сигнал."""
    if card.appointments_count == 0:
        return "📊 <b>Активность:</b> ни одного приёма — клиника так и не начала работать"
    parts = []
    if card.type is ClinicType.CLINIC:
        parts.append(plural(card.staff_count, "сотрудник", "сотрудника", "сотрудников"))
    parts.append(plural(card.patients_count, "пациент", "пациента", "пациентов"))
    parts.append(plural(card.appointments_count, "приём", "приёма", "приёмов"))
    return f"📊 <b>Активность:</b> {esc(' · '.join(parts))}"


def client_registered_text(card: ClientCard) -> str:
    """Карточка самостоятельной регистрации (соло-врач по OTP) с пробным периодом."""
    title = "соло-врач" if card.type is ClinicType.SOLO else "клиника"
    lines = [f"🆕 <b>Новая регистрация: {title}</b>", "", *_who(card), *_contacts(card)]
    lines.append("✅ Телефон подтверждён кодом")
    if card.trial_ends_at is not None:
        lines.extend(["", f"🎁 <b>Пробный период:</b> до {team_date(card.trial_ends_at)}"])
    lines.extend(
        [
            "",
            f"🕒 {team_datetime(card.registered_at)}",
            f"#регистрация {_kind_tag(card)}",
        ]
    )
    return "\n".join(lines)


def trial_expired_text(card: ClientCard) -> str:
    """Карточка клиента, у которого только что кончился пробный период."""
    lines = ["⌛ <b>Закончился пробный период</b>", "", *_who(card), *_contacts(card), ""]
    if card.trial_started_at is not None and card.trial_ends_at is not None:
        period = f"{team_date(card.trial_started_at)} — {team_date(card.trial_ends_at)}"
        lines.append(f"📅 <b>Триал:</b> {period}")
    lines.append(_activity(card))
    if not card.owner_phone:
        lines.append("⚠️ <i>У клиники нет активного владельца — телефона для связи нет.</i>")
    lines.extend(
        [
            "",
            "<i>Клиника в режиме чтения: данные на месте, но записывать уже нельзя. "
            "Самое время позвонить и предложить оплату.</i>",
            f"#триал_закончился {_kind_tag(card)}",
        ]
    )
    return "\n".join(lines)


def card_buttons(card: ClientCard) -> tuple[ChatButton, ...]:
    """Кнопки «написать» владельцу; телефона нет — карточка без кнопок."""
    return contact_buttons(card.owner_phone) if card.owner_phone else ()
