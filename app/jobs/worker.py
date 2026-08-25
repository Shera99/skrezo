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

from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.jobs import DISPATCH_PUSH, SEND_NOTIFICATION, SEND_TEAM_NOTIFICATION
from app.jobs.tasks.maintenance import cleanup_job_runs, heartbeat
from app.jobs.tasks.notifications import send_notification, send_team_notification
from app.jobs.tasks.push import dispatch_push
from app.jobs.tasks.subscriptions import (
    expire_paid_periods,
    expire_trials,
    warn_period_ending,
    warn_trial_ending,
)


class WorkerSettings:
    """Настройки arq-воркера."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions: ClassVar[list[Any]] = [
        # Доставка повторяется: провайдер мог не ответить. Таймаут с запасом на
        # всю цепочку каналов (каждый со своим таймаутом запроса).
        func(send_notification, name=SEND_NOTIFICATION, max_tries=5, timeout=60),
        func(send_team_notification, name=SEND_TEAM_NOTIFICATION, max_tries=5, timeout=60),
        # Рассылка пушей: недоставку повторяем (провайдер мог не ответить), но
        # реже, чем текст, — тихий sync.day не критичен, а мёртвый токен диспетчер
        # снимает сам, без ретраев.
        func(dispatch_push, name=DISPATCH_PUSH, max_tries=3, timeout=60),
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
        # Каждую минуту (все поля-заглушки, секунда 0) и сразу на старте — иначе
        # только что поднятый воркер до минуты выглядит мёртвым.
        cron(heartbeat, run_at_startup=True),
    ]
