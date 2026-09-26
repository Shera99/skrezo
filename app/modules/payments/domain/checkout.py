"""Возврат плательщика со страницы FreedomPay обратно в кабинет.

Кабинет присылает вместе с оплатой ``returnMode`` — как вернуть человека после
оплаты. С компьютера удобнее сразу перекинуть обратно, с телефона — показать
кнопку «Вернуться»: страница провайдера открыта в браузере, а кабинет может
жить в приложении, и автоматический переход там выглядит как сбой.

⚠️ ``AUTOGET`` — значение из API PayBox, на котором вырос FreedomPay; нынешняя
документация провайдера перечисляет только ``GET`` и ``POST``. Поэтому отказ
провайдера на ``AUTOGET`` не роняет оплату: запрос повторяется с кнопкой
(см. ``StartSubscriptionPaymentUseCase``).
"""

from __future__ import annotations

from typing import Literal

ReturnMode = Literal["auto", "button"]

#: Кнопка «Вернуться» на странице провайдера (телефон; режим по умолчанию).
METHOD_BUTTON = "GET"
#: Сразу перекинуть обратно без кнопки (компьютер).
METHOD_AUTO = "AUTOGET"


def return_method(mode: ReturnMode | None, *, auto_enabled: bool = True) -> str:
    """Метод возврата для ``pg_success_url_method`` / ``pg_failure_url_method``.

    ``auto_enabled=False`` (настройка ``FREEDOMPAY_AUTO_RETURN``) — возврат
    везде кнопкой: на случай, если провайдер не принимает ``AUTOGET``.
    """
    return METHOD_AUTO if mode == "auto" and auto_enabled else METHOD_BUTTON
