"""Arq-воркер фоновых задач: расписание и точка входа.

Запускается как отдельный процесс: ``make worker`` локально, сервис ``worker`` в
``docker-compose.yml``. Точка входа — ``arq app.jobs.worker.WorkerSettings``.

Здесь только сборка: сами задачи лежат в ``app/jobs/tasks/``, а их работа — в
use-case'ах модулей. Воркер — тонкий адаптер, и держать в нём логику значит
получить код, который нельзя вызвать из API и неудобно тестировать.

Задачи двух родов:

* **по расписанию** — состояние подписок и уборка журнала. Они идемпотентны, и
  arq не повторяет их при падении (``max_tries=1`` у cron): повтор по графику
  безопаснее ретрая поверх недоделанного;
* **по событию** — доставка сообщений, поставленная из обработчика запроса. Вот
  их повторять нужно: провайдер мог просто не ответить.

Каждый запуск пишется в ``job_run`` — по журналу видно и что задача упала, и что
она вообще не запускалась. Исключение одно: ``heartbeat``, он сам и есть признак
жизни и в журнале был бы шумом на 1440 строк в сутки.

⚠️ Доступ клиник воркер **не закрывает**: и конец триала, и конец оплаченного
периода считает политика подписки по датам. Упавший воркер не должен раздавать
бесплатный доступ — задачи лишь приводят статус в БД к реальности.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.jobs import (
    AI_REPLY,
    PROCESS_WA_INBOUND,
    RUN_AI_REGRESSION,
    SEND_NOTIFICATION,
    SEND_TEAM_NOTIFICATION,
    SUMMARIZE_CONVERSATION,
)
from app.jobs.dispatcher_loop import run_dispatcher_loop
from app.jobs.tasks.ai import ai_reply, run_ai_regression, summarize_conversation
from app.jobs.tasks.ai_health import check_ai_health
from app.jobs.tasks.digests import send_daily_digests
from app.jobs.tasks.maintenance import cleanup_job_runs, heartbeat
from app.jobs.tasks.notifications import send_notification, send_team_notification
from app.jobs.tasks.notifications_cleanup import cleanup_notifications
from app.jobs.tasks.subscriptions import (
    expire_paid_periods,
    expire_trials,
    warn_period_ending,
    warn_trial_ending,
)
from app.jobs.tasks.whatsapp import close_stale_conversations, process_wa_inbound


async def _startup(ctx: dict[str, Any]) -> None:
    """Поднять цикл диспетчера событий рядом с воркером (см. dispatcher_loop)."""
    ctx["event_dispatcher_loop"] = asyncio.create_task(run_dispatcher_loop())


async def _shutdown(ctx: dict[str, Any]) -> None:
    task = ctx.pop("event_dispatcher_loop", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class WorkerSettings:
    """Настройки arq-воркера."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Ф8: задания ответа почти всё время ждут модель — держим их десятками,
    # пул БД под это расширен (db_pool_size / db_max_overflow).
    max_jobs = settings.worker_max_jobs
    on_startup = _startup
    on_shutdown = _shutdown
    functions: ClassVar[list[Any]] = [
        # Доставка повторяется: провайдер мог не ответить. Таймаут с запасом на
        # всю цепочку каналов (каждый со своим таймаутом запроса).
        func(send_notification, name=SEND_NOTIFICATION, max_tries=5, timeout=60),
        func(send_team_notification, name=SEND_TEAM_NOTIFICATION, max_tries=5, timeout=60),
        # Разбор вебхука WhatsApp: повтор безопасен (дедуп по wa_message_id), а
        # таймаут с запасом на скачивание медиа с серверов Meta.
        func(process_wa_inbound, name=PROCESS_WA_INBOUND, max_tries=5, timeout=120),
        # Ф8: ответ ассистента — своим заданием на диалог. Повтор безопасен:
        # координатор не отвечает на уже отвеченное. Таймаут — на все вызовы
        # модели одного хода с запасом.
        func(
            ai_reply,
            name=AI_REPLY,
            max_tries=2,
            timeout=settings.ai_reply_job_timeout,
        ),
        # Резюме закрытого диалога — по одному, а не пачкой в cron: пачка не
        # укладывалась в таймаут, и закрытые диалоги оставались без резюме.
        func(summarize_conversation, name=SUMMARIZE_CONVERSATION, max_tries=2, timeout=120),
        # Ф6: прогон регрессии клиники — до сотен вызовов модели; повтор безопасен,
        # оценённые кейсы не оцениваются второй раз.
        func(
            run_ai_regression,
            name=RUN_AI_REGRESSION,
            max_tries=2,
            timeout=settings.ai_regression_job_timeout,
        ),
        expire_trials,
        expire_paid_periods,
        warn_trial_ending,
        warn_period_ending,
        cleanup_job_runs,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        # Раз в час, а не раз в сутки: срок кончается в конкретный момент, и сутки
        # расхождения статуса заметны и клиенту, и в дашборде. Минуты разные, чтобы
        # две задачи не начинали разом одну и ту же таблицу.
        cron(expire_trials, minute=5),
        cron(expire_paid_periods, minute=10),
        # 03:15 UTC — это 09:15 по Бишкеку: предупреждение приходит утром рабочего
        # дня, а не ночью.
        cron(warn_trial_ending, hour=3, minute=15),
        # Отдельной задачей, а не вместе с триалом: падение одной не должно
        # оставлять без предупреждения клиентов другой.
        cron(warn_period_ending, hour=3, minute=20),
        cron(cleanup_job_runs, hour=2, minute=30),
        # Сроки хранения контура уведомлений (§14): телеметрия 3 мес, события 12.
        cron(cleanup_notifications, hour=2, minute=40),
        # Диалог WhatsApp без сообщений сутки — закрыт. Полчаса — компромисс:
        # правило «24 часа» не требует точности до минуты.
        cron(close_stale_conversations, minute={11, 41}),
        # §13.5: тихие отказы контура ассистента ловятся только расписанием.
        cron(check_ai_health, minute=set(range(0, 60, 5))),
        # Сводки дня: тик раз в 5 минут, окно совпадения 15 — момент сводки
        # (за час до открытия / в закрытие) не проскочит; дубль давит push_event.
        cron(send_daily_digests, minute=set(range(0, 60, 5))),
        # Каждую минуту (все поля-заглушки, секунда 0) и сразу на старте — иначе
        # только что поднятый воркер до минуты выглядит мёртвым.
        cron(heartbeat, run_at_startup=True),
    ]
