"""Почему WhatsApp не доставил сообщение — понятным языком и с решением «что дальше».

Meta отвечает на отправку кодом ошибки (синхронно — 400 на запрос, или позже —
статусом ``failed`` в вебхуке). Для клиники «Graph API 400» ничего не значит:
номер не в списке разрешённых, у номера нет WhatsApp и закрытое окно требуют
разных действий. Здесь код превращается в объяснение для чата клиники и в
признак, постоянная ли это ошибка.

Постоянная (``permanent``) — повтор того же сообщения не поможет, пока кто-то
не исправит причину: ассистент передаёт диалог человеку с «липкой» причиной
``delivery_failed`` и не тратит модель на каждое новое сообщение пациента.
Временная — сбой сети или лимит Meta: прежняя передача ``tool_error``, бот
попробует снова после автовозврата.

Коды — по документации WhatsApp Cloud API (Error codes).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    """Отказ доставки: объяснение для клиники и можно ли надеяться на повтор."""

    text: str
    permanent: bool
    code: int | None = None


#: Код Meta → (объяснение для клиники, постоянная ли ошибка).
_KNOWN: dict[int, tuple[str, bool]] = {
    131030: (
        "номер пациента не в списке разрешённых получателей — WhatsApp клиники "
        "подключён в тестовом режиме Meta",
        True,
    ),
    131026: (
        "сообщение не доставляется на этот номер: у него нет WhatsApp, приложение "
        "устарело или пациент не принял условия WhatsApp",
        True,
    ),
    131047: (
        "прошло больше 24 часов с последнего сообщения пациента — написать можно только шаблоном",
        True,
    ),
    131021: ("нельзя отправить сообщение на номер самой клиники", True),
    131031: ("аккаунт WhatsApp клиники заблокирован или ограничен Meta", True),
    131042: ("у Meta проблема с оплатой WhatsApp Business клиники", True),
    131045: ("номер WhatsApp клиники не зарегистрирован в Cloud API", True),
    133010: ("номер WhatsApp клиники не зарегистрирован в Cloud API", True),
    131048: ("Meta ограничила отправку из-за жалоб на спам — проверьте качество номера", True),
    131051: ("WhatsApp не поддерживает такой тип сообщения", True),
    190: ("доступ к WhatsApp клиники истёк — переподключите WhatsApp в настройках", True),
    10: ("у приложения нет прав на этот номер WhatsApp — переподключите WhatsApp", True),
    200: ("у приложения нет прав на этот номер WhatsApp — переподключите WhatsApp", True),
    130429: ("превышен лимит отправки Meta — сообщение уйдёт позже", False),
    131056: ("слишком много сообщений этому пациенту подряд — Meta просит подождать", False),
    131000: ("сбой на стороне WhatsApp — сообщение уйдёт позже", False),
    131016: ("WhatsApp временно недоступен — сообщение уйдёт позже", False),
    1: ("сбой на стороне WhatsApp — сообщение уйдёт позже", False),
    2: ("WhatsApp временно недоступен — сообщение уйдёт позже", False),
}

_PREFIX = "WhatsApp не доставил сообщение: "


def describe_failure(
    *, status: int | None, code: int | None, detail: str | None = None
) -> DeliveryFailure:
    """Отказ Meta (HTTP-статус, код, текст) → объяснение и постоянство.

    Незнакомый код: 4xx (кроме 429) — постоянная ошибка запроса, 5xx, 429 и
    обрыв связи — временная.
    """
    if code is not None and code in _KNOWN:
        text, permanent = _KNOWN[code]
        return DeliveryFailure(text=_PREFIX + text, permanent=permanent, code=code)
    if status is None:
        return DeliveryFailure(text=_PREFIX + "нет связи с WhatsApp", permanent=False, code=code)
    permanent = 400 <= status < 500 and status != 429
    tail = f"код {code}" if code is not None else f"HTTP {status}"
    if detail:
        tail += f", {detail}"
    return DeliveryFailure(
        text=_PREFIX + f"WhatsApp отклонил сообщение ({tail})", permanent=permanent, code=code
    )
