"""Ошибки голосового помощника приложения: тело запроса и ответ провайдера.

Доменные, без HTTP: прикладной слой сам решает, каким статусом ответить —
неверное тело (422) не должно размыкать предохранитель, а таймаут модели
(503) — должен.
"""

from __future__ import annotations


class LlmRequestInvalidError(Exception):
    """Тело запроса не по контракту: задача, поля, части, инструменты, размер."""


class LlmUpstreamError(Exception):
    """Провайдер модели не дал ответа (общий предок ошибок ниже)."""


class LlmUpstreamTimeoutError(LlmUpstreamError):
    """Модель не ответила за таймаут задачи."""


class LlmUpstreamBusyError(LlmUpstreamError):
    """Провайдер отказал по квоте (HTTP 429); ``retry_after`` — его подсказка."""

    def __init__(self, retry_after: int | None) -> None:
        super().__init__(f"квота провайдера, повтор через {retry_after or '?'} с")
        self.retry_after = retry_after


class LlmUpstreamRejectedError(LlmUpstreamError):
    """Провайдер не принял само тело (HTTP 400) — повтор того же не поможет."""


class LlmUpstreamFailedError(LlmUpstreamError):
    """Сбой провайдера или сети, неверный ключ, ответ не JSON."""
