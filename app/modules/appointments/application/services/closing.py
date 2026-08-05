"""Правила закрытия приёма: позиции обязательны, «бесплатно» — осознанно.

Закрыть приём можно двумя способами — ручкой «завершить и оплатить»
(``POST /clinic/appointments/{id}/complete``) и обычной правкой
(``PATCH`` со ``status: completed``). Правило одно на оба пути, поэтому живёт
здесь, а не внутри одного из use-case'ов: иначе PATCH становится дырой, через
которую нулевой чек закрывается в обход проверки.

Смысл правила: приём без единой позиции закрывается счётом на ноль и в ленте
неотличим от обычного. Бесплатный приём бывает (гарантийный осмотр, доработка),
но он должен быть решением с пояснением — флагом ``noCharge``, а не следствием
незаполненной формы.
"""

from __future__ import annotations

from app.core.errors import ValidationAppError


def ensure_no_charge_valid(*, has_items: bool, no_charge: bool, comment: str | None) -> None:
    """Проверить сам флаг «бесплатно»: с пояснением и только у приёма без позиций.

    Позиции — это оказанные работы с ценой; отдавать их бесплатно оптом флагом
    нельзя, иначе ``noCharge`` превращается в скрытую 100% скидку мимо права
    ``cash.discount``. Нужна скидка — она делается по позициям.
    """
    if not no_charge:
        return
    if not comment:
        raise ValidationAppError(
            "Бесплатный приём требует пояснения (noChargeComment)",
            code="no_charge_comment_required",
        )
    if has_items:
        raise ValidationAppError(
            "У приёма есть позиции: noCharge — только для приёма без позиций",
            code="no_charge_with_items",
        )


def ensure_closable(*, has_items: bool, no_charge: bool, comment: str | None) -> None:
    """Проверить, что приём можно закрыть: есть позиции либо явное «бесплатно»."""
    ensure_no_charge_valid(has_items=has_items, no_charge=no_charge, comment=comment)
    if not has_items and not no_charge:
        raise ValidationAppError(
            "Приём без позиций закрыть нельзя: добавьте услуги "
            "либо закройте как бесплатный (noCharge с пояснением)",
            code="appointment_without_items",
        )
