"""Тексты сообщений о состоянии подписки.

Владельцу — на телефон, команде Skrezo — в чат. Тексты разные не для красоты:
владельцу нужно понять, что делать, а команде — по какой клинике звонить.

Слово «оплата» намеренно без ссылки и без суммы: платёжного контура ещё нет,
и обещать кнопку, которой не существует, значит получить звонок «а где?».
"""

from __future__ import annotations


def _days_word(days: int) -> str:
    """«1 день», «3 дня», «7 дней» — иначе сообщение выглядит машинным."""
    if days % 10 == 1 and days % 100 != 11:
        return "день"
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def trial_ending_text(clinic_name: str, days_left: int) -> str:
    """Владельцу: триал скоро кончится."""
    return (
        f"Skrezo: пробный период клиники «{clinic_name}» заканчивается через "
        f"{days_left} {_days_word(days_left)}. После этого записи и карты пациентов "
        "останутся доступны только для просмотра. Свяжитесь с нами, чтобы продолжить."
    )


def trial_expired_text(clinic_name: str) -> str:
    """Владельцу: триал кончился, клиника в режиме чтения."""
    return (
        f"Skrezo: пробный период клиники «{clinic_name}» закончился. "
        "Данные на месте, но добавлять и менять записи больше нельзя. "
        "Свяжитесь с нами, чтобы продолжить работу."
    )


def team_trial_ending_text(clinic_name: str, days_left: int) -> str:
    """Команде: у клиники кончается триал — повод позвонить."""
    return f"⏳ Триал заканчивается через {days_left} {_days_word(days_left)}: {clinic_name}"


def paid_period_expired_text(clinic_name: str) -> str:
    """Владельцу: оплаченный период кончился, клиника в режиме чтения."""
    return (
        f"Skrezo: оплаченный период клиники «{clinic_name}» закончился. "
        "Данные на месте, но добавлять и менять записи больше нельзя. "
        "Свяжитесь с нами, чтобы продлить доступ."
    )


def team_paid_periods_expired_text(clinic_names: list[str]) -> str:
    """Команде: у кого кончился оплаченный период (список одним сообщением)."""
    lines = ["💳 Оплаченный период закончился, клиники перешли в режим чтения:"]
    lines.extend(f"• {name}" for name in clinic_names)
    return "\n".join(lines)


def team_trials_expired_text(clinic_names: list[str]) -> str:
    """Команде: сводка по клиникам, у которых триал кончился только что.

    Одним сообщением, а не по одному на клинику: джоба ходит по всем сразу, и
    десяток отдельных сообщений в чате читается хуже, чем список.
    """
    lines = ["🔒 Триал закончился, клиники перешли в режим чтения:"]
    lines.extend(f"• {name}" for name in clinic_names)
    return "\n".join(lines)


def period_ending_text(clinic_name: str, days_left: int) -> str:
    """Владельцу: оплаченный период скоро кончится."""
    return (
        f"Skrezo: оплаченный период клиники «{clinic_name}» заканчивается через "
        f"{days_left} {_days_word(days_left)}. Продлите доступ, чтобы записи и "
        "касса продолжили работать."
    )


def team_period_ending_text(clinic_name: str, days_left: int) -> str:
    """Команде: у клиники кончается оплата — повод выставить счёт."""
    return f"💳 Оплата заканчивается через {days_left} {_days_word(days_left)}: {clinic_name}"


def balance_low_text(clinic_name: str, shortfall_tiyin: int, days_left: int) -> str:
    """Владельцу: автопродление не прошло — сколько положить и до какого срока."""
    som = shortfall_tiyin // 100
    when = "сегодня" if days_left <= 0 else f"в ближайшие {days_left} {_days_word(days_left)}"
    return (
        f"Skrezo: подписка «{clinic_name}» не продлилась автоматически — на балансе не "
        f"хватает {som} сом. Пополните баланс {when}, и доступ продлится сам."
    )


def team_balance_low_text(clinic_name: str, shortfall_tiyin: int, days_left: int) -> str:
    """Команде: у кого автопродление упёрлось в баланс."""
    return (
        f"Автопродление: «{clinic_name}» — не хватает {shortfall_tiyin // 100} сом, "
        f"до конца периода {max(days_left, 0)} дн."
    )
