"""Настройка структурного логирования (structlog).

В production — JSON-логи, локально — человекочитаемый цветной вывод.
"""

from __future__ import annotations

import logging

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """Сконфигурировать structlog + стандартный logging."""
    logging.basicConfig(
        format="%(message)s",
        level=logging.DEBUG if settings.app_debug else logging.INFO,
    )
    # httpx на INFO пишет полный URL каждого запроса — вместе с одноразовыми
    # кодами и секретами в строке запроса (обмен кода Embedded Signup несёт
    # секрет приложения Meta). Вызовы внешних API видны по нашим событиям.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.is_production:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.app_debug else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
