# Команды должны оставаться одной строкой: так их удобно копировать.
# ruff: noqa: E501
"""Пульт сервера Skrezo: разделы и команды — источник для ``docs/SERVER_OPS.html``.

Команды правятся здесь, затем страница пересобирается: ``make server-ops``
(или ``uv run python scripts/server_ops/build.py``). Сам HTML руками не правится.

Команда — :class:`C`:

- ``title`` — что проверяем или делаем, заголовок строки;
- ``desc`` — что делает команда; текст в обратных кавычках станет моноширинным;
- ``cmd`` — сама команда, одна строка или несколько; ``{{DOMAIN}}`` заменяется
  доменом API из поля на странице;
- ``expect`` — что должно получиться (строка «Ожидается»), можно оставить пустым;
- ``flags`` — пометки: ``(WARN, "простой")``, ``(DANGER, "меняет данные")``;
- ``kw`` — дополнительные слова для поиска: синонимы, английские названия.

Раздел — :class:`S`: ``id`` (якорь в адресе), ``nav`` (пункт оглавления),
``title``, ``lead`` (одна-две фразы), ``meta`` (метки: юниты, порты, файлы),
``items`` (команды). Команды рассчитаны на пользователя с sudo и короткие
функции из раздела «Подготовка» (``rcli``, ``pgsu``, ``pgapp``, ``pgb``, ``skrun``).
"""

from dataclasses import dataclass, field

DANGER = "danger"  # удаляет или перезаписывает данные
WARN = "warn"  # простой, перезапуск или иная осторожность

Flag = tuple[str, str]


@dataclass
class C:
    """Команда пульта: заголовок, описание, сама команда, ожидаемый результат."""

    title: str
    desc: str
    cmd: str
    expect: str = ""
    flags: tuple[Flag, ...] = ()
    kw: str = ""


@dataclass
class S:
    """Раздел пульта: сервис или тема со своим списком команд."""

    id: str
    nav: str
    title: str
    lead: str
    meta: list[str] = field(default_factory=list)
    items: list[C] = field(default_factory=list)
    #: Имя статичного блока из build.py (таблица), выводится перед командами.
    static: str = ""
    kw: str = ""


SECTIONS: list[S] = []

# ── Подготовка ────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "prep",
        "Подготовка",
        "Подготовка",
        "Один раз на вашем пользователе. Остальные разделы пользуются этими короткими командами: "
        "пароли берутся из `.env` в момент вызова и не попадают в историю.",
        meta=["~/.bashrc"],
        items=[
            C(
                "Завести короткие команды",
                "Дописывает в `~/.bashrc` функции: `rcli` для Redis, `pgsu` и `pgapp` для базы, `pgb` для "
                "консоли PgBouncer, `skrun` и `skr` для команд проекта от имени `skrezo`. Если `skr` уже "
                "заведён раньше, повтор безвреден.",
                r"""cat >> ~/.bashrc <<'EOF'

# ── Skrezo: короткие команды для проверок сервера ──
skenv() { sudo grep -E "^$1=" /opt/skrezo/app/.env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//'; }
rcli()  { redis-cli -u "$(skenv REDIS_URL)" --no-auth-warning "$@"; }
pgsu()  { (cd /tmp && sudo -u postgres psql -d skrezo "$@"); }
pgapp() { psql "$(skenv DATABASE_URL | sed 's/+asyncpg//')" "$@"; }
pgb()   { psql "$(skenv DATABASE_URL | sed 's/+asyncpg//; s#/[^/]*$#/pgbouncer#')" "$@"; }
skrun() { sudo -u skrezo -H bash -c "cd /opt/skrezo/app && source .venv/bin/activate && $(printf '%q ' "$@")"; }
alias skr='sudo -iu skrezo bash -c "cd /opt/skrezo/app && source .venv/bin/activate && exec bash"'
EOF
source ~/.bashrc""",
                "Без вывода. С этого момента команды на странице работают из-под вашего пользователя.",
                kw="alias функции bashrc ярлыки",
            ),
            C(
                "Проверить короткие команды",
                "Redis, база через пулер и консоль PgBouncer отвечают с паролями из `.env`.",
                r"""rcli PING && pgapp -At -c 'SELECT 1' && pgb -At -c 'SHOW VERSION;'""",
                "`PONG`, `1` и строка с версией PgBouncer.",
            ),
        ],
    )
)

# ── Каждый день ───────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "daily",
        "Каждый день",
        "Каждый день",
        "Самое частое: выкатить версию, посмотреть состояние, найти ошибку.",
        meta=["deploy.sh", "skrezo.target"],
        items=[
            C(
                "Выкатить новую версию",
                "Код из ветки main, юниты systemd, зависимости, миграции. Потом API перезапускается по "
                "одному процессу с проверкой здоровья, затем воркеры. Сайт не прерывается.",
                r"""sudo /opt/skrezo/app/deploy/bin/deploy.sh""",
                "В конце строка `готово: <коммит>`. Если процесс API не поднялся, скрипт остановится и покажет его журнал.",
                kw="деплой релиз обновить выкатить",
            ),
            C(
                "Откатиться на тег или коммит",
                "То же самое, но на указанную версию. Миграции вниз сами не откатываются: см. «Деплой и миграции».",
                r"""sudo /opt/skrezo/app/deploy/bin/deploy.sh v1.4.0""",
                kw="откат rollback версия тег",
            ),
            C(
                "Состояние всех сервисов Skrezo",
                "Одна строка на юнит: API, воркеры, группа и таймер бэкапа.",
                r"""systemctl list-units 'skrezo-*' --all --no-pager""",
                "У API и воркеров `active running`, у таймера `active waiting`.",
            ),
            C(
                "Подробный статус с последними строками логов",
                "Память, время работы и десять последних строк журнала каждого процесса.",
                r"""systemctl status 'skrezo-*' --no-pager""",
            ),
            C(
                "Перезапустить всё сразу",
                "API и воркеры перезапускаются одновременно, сайт недоступен несколько секунд. Без простоя: `deploy.sh` или цикл из раздела «Сервисы systemd».",
                r"""sudo systemctl restart skrezo.target""",
                flags=((WARN, "простой"),),
                kw="рестарт",
            ),
            C(
                "Живой лог всего Skrezo",
                "Все процессы API и воркеров в одном потоке. Выход: Ctrl+C.",
                r"""sudo journalctl -u 'skrezo-*' -f""",
                kw="логи журнал",
            ),
            C(
                "Ошибки за последний час",
                "Строки с ошибками и трассировками из всех процессов Skrezo.",
                r"""sudo journalctl -u 'skrezo-*' --since '1 hour ago' --no-pager | grep -Ei 'error|exception|traceback|failed'""",
                "Пусто: ошибок не было.",
            ),
            C(
                "Сайт отвечает снаружи",
                "Запрос из интернета через nginx и HTTPS до процесса API.",
                r"""curl -s https://{{DOMAIN}}/health; echo""",
                '`{"status":"ok"}`',
                kw="health здоровье",
            ),
        ],
    )
)

# ── Сервисы systemd ───────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "services",
        "Сервисы systemd",
        "Сервисы systemd",
        "API и воркеры запускаются шаблонами: номер после @ — порт процесса API или номер воркера. "
        "Все они входят в `skrezo.target` и поднимаются сами после перезагрузки и падений.",
        meta=["skrezo-api@.service", "skrezo-worker@.service", "skrezo.target", "Restart=always"],
        items=[
            C(
                "Статус одного процесса API",
                "Порт в имени выбирает процесс: 8001 или 8002.",
                r"""systemctl status skrezo-api@8001 --no-pager""",
            ),
            C(
                "Перезапустить API без простоя",
                "Процессы по очереди: следующий начинается, только когда предыдущий ответил на `/health`. Нужен после правки `.env`.",
                r"""for p in 8001 8002; do sudo systemctl restart skrezo-api@$p; until curl -fsS http://127.0.0.1:$p/health >/dev/null 2>&1; do sleep 1; done; echo "$p готов"; done""",
                "`8001 готов`, затем `8002 готов`.",
                kw="рестарт без простоя",
            ),
            C(
                "Перезапустить воркеры",
                "Текущие задания отменяются и возвращаются в очередь, их доделает другой воркер или этот после старта. Повтор безопасен.",
                r"""sudo systemctl restart skrezo-worker@1 skrezo-worker@2""",
                kw="рестарт arq",
            ),
            C(
                "Остановить всё",
                "Сайт и фоновые задачи останавливаются до команды запуска.",
                r"""sudo systemctl stop skrezo.target""",
                flags=((WARN, "простой"),),
            ),
            C(
                "Запустить всё",
                "Поднимает все процессы API и воркеров группы.",
                r"""sudo systemctl start skrezo.target""",
            ),
            C(
                "Автозапуск после перезагрузки включён",
                "Каждый юнит должен быть включён, иначе после перезагрузки сервера он не поднимется.",
                r"""systemctl is-enabled skrezo.target skrezo-api@8001 skrezo-api@8002 skrezo-worker@1 skrezo-worker@2 skrezo-backup.timer""",
                "`enabled` в каждой строке.",
                kw="enable автозапуск",
            ),
            C(
                "Что входит в группу Skrezo",
                "Дерево зависимостей `skrezo.target`.",
                r"""systemctl list-dependencies skrezo.target --no-pager""",
            ),
            C(
                "Сколько раз процесс падал",
                "`NRestarts` больше нуля: процесс падал и systemd поднимал его снова. Причина в журнале.",
                r"""systemctl show skrezo-api@8001 -p NRestarts -p ActiveEnterTimestamp -p ExecMainStatus""",
                kw="рестарты падения",
            ),
            C(
                "Как сейчас настроен юнит",
                "Файл юнита и все drop-in поверх него.",
                r"""systemctl cat skrezo-api@8001""",
            ),
            C(
                "Своя правка юнита, которую не сотрёт деплой",
                "Открывает drop-in `override.conf`. Файлы самих юнитов `deploy.sh` перезаписывает из репозитория, а drop-in не трогает. После сохранения перезапустите процессы.",
                r"""sudo systemctl edit skrezo-api@.service""",
                kw="override drop-in",
            ),
            C(
                "Перечитать юниты после ручной правки",
                "Нужно после изменения файлов в `/etc/systemd/system`.",
                r"""sudo systemctl daemon-reload""",
            ),
            C(
                "Упавшие юниты во всей системе",
                "Любой сервис сервера, который не смог запуститься.",
                r"""systemctl --failed --no-pager""",
                "`0 loaded units listed.`",
            ),
        ],
    )
)

# ── Логи ──────────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "logs",
        "Логи",
        "Логи",
        "Вывод всех процессов Skrezo собирает journald. Имена юнитов принимают шаблоны: `skrezo-*`, `skrezo-api@*`.",
        meta=["journalctl", "лимит 2 ГБ"],
        items=[
            C(
                "Живой лог одного процесса API",
                "Выход: Ctrl+C.",
                r"""sudo journalctl -u skrezo-api@8001 -f""",
            ),
            C(
                "Живой лог воркеров",
                "Оба воркера в одном потоке: задания, cron, диспетчер событий.",
                r"""sudo journalctl -u 'skrezo-worker@*' -f""",
                kw="arq",
            ),
            C(
                "Последние 200 строк",
                "Без прокрутки и без ожидания новых строк.",
                r"""sudo journalctl -u skrezo-api@8001 -n 200 --no-pager""",
            ),
            C(
                "Последние 30 минут, только текст",
                "Без даты, хоста и имени процесса в начале строк.",
                r"""sudo journalctl -u 'skrezo-*' --since '30 min ago' -o cat --no-pager""",
            ),
            C(
                "Найти событие по имени",
                "Имена событий в логах: `push_delivery_failed`, `realtime_listener_reconnect`, `fcm_channel_init_failed`, `event_dispatcher_tick_failed` и другие.",
                r"""sudo journalctl -u 'skrezo-*' --since today --no-pager | grep push_delivery_failed""",
            ),
            C(
                "Что было перед перезагрузкой сервера",
                "`-b -1` показывает предыдущую загрузку: видно, что происходило перед падением.",
                r"""sudo journalctl -u 'skrezo-*' -b -1 --no-pager | tail -100""",
                kw="падение сервера ребут",
            ),
            C(
                "Сколько места занимают журналы",
                "Потолок задан при установке, 2 ГБ.",
                r"""journalctl --disk-usage""",
            ),
            C(
                "Ужать журналы",
                "Удаляет самые старые записи, пока журналы не уместятся в 1 ГБ.",
                r"""sudo journalctl --vacuum-size=1G""",
                flags=((DANGER, "удаляет старые логи"),),
                kw="диск место",
            ),
        ],
    )
)

# ── API ───────────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "api",
        "API",
        "API",
        "Два процесса uvicorn на 127.0.0.1. Наружу их выставляет nginx, напрямую из интернета они недоступны.",
        meta=["127.0.0.1:8001", "127.0.0.1:8002", "/health", "/docs", "/redoc"],
        items=[
            C(
                "Каждый процесс напрямую",
                "Запрос к процессам мимо nginx: так видно, какой из двух не отвечает.",
                r"""for p in 8001 8002; do printf '%s: ' $p; curl -s http://127.0.0.1:$p/health; echo; done""",
                '`8001: {"status":"ok"}` и то же для 8002.',
            ),
            C(
                "Через nginx и HTTPS со временем ответа",
                "Код ответа и полное время запроса снаружи.",
                r"""curl -s -o /dev/null -w 'код %{http_code}, %{time_total} с\n' https://{{DOMAIN}}/health""",
                "`код 200` и доли секунды.",
            ),
            C(
                "Документация открывается",
                "Swagger и ReDoc отдаются через nginx.",
                r"""curl -s -o /dev/null -w 'docs %{http_code}\n' https://{{DOMAIN}}/docs; curl -s -o /dev/null -w 'redoc %{http_code}\n' https://{{DOMAIN}}/redoc""",
                "`docs 200` и `redoc 200`.",
                kw="swagger redoc openapi",
            ),
            C(
                "Сколько путей в схеме API",
                "Быстрая сверка после деплоя: число путей совпадает с локальной схемой.",
                r"""curl -s https://{{DOMAIN}}/openapi.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["paths"]), "путей")'""",
                kw="openapi ручки эндпоинты",
            ),
            C(
                "Кто слушает порты API",
                "Оба порта только на 127.0.0.1, процессы uvicorn.",
                r"""sudo ss -tlnp | grep -E ':800[12] '""",
            ),
            C(
                "Какая версия кода запущена",
                "Последний коммит в каталоге приложения.",
                r"""sudo -u skrezo -H git -C /opt/skrezo/app log --oneline -1""",
                kw="версия коммит",
            ),
            C(
                "Нет ли ручных правок кода на сервере",
                "Любая строка в выводе означает, что файл изменён прямо на сервере. Деплой на таком каталоге может остановиться.",
                r"""sudo -u skrezo -H git -C /opt/skrezo/app status --short""",
                "Пусто.",
            ),
        ],
    )
)

# ── Сокет ─────────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "socket",
        "Сокет и шина",
        "Сокет и шина realtime",
        "Socket.IO живёт внутри процессов API по пути `/socket.io/`. Процессы передают друг другу события "
        "через Postgres: LISTEN/NOTIFY, канал `realtime`, напрямую на 5432 мимо PgBouncer. nginx держит "
        "клиента на одном процессе через `ip_hash`.",
        meta=["/socket.io/", "канал realtime", "DATABASE_DIRECT_URL", "ip_hash"],
        items=[
            C(
                "Рукопожатие через nginx",
                "Первый шаг подключения кабинета: сервер выдаёт идентификатор сессии.",
                r"""curl -s "https://{{DOMAIN}}/socket.io/?EIO=4&transport=polling"; echo""",
                '`0{"sid":"…","upgrades":["websocket"],…}`',
                kw="socketio websocket реалтайм",
            ),
            C(
                "Переход на WebSocket через nginx",
                "Проверяет, что nginx пропускает заголовки Upgrade. Команда сама завершится через 3 секунды.",
                r"""curl -si --http1.1 --max-time 3 -H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" "https://{{DOMAIN}}/socket.io/?EIO=4&transport=websocket" | head -1""",
                "`HTTP/1.1 101 Switching Protocols`",
                kw="websocket upgrade 101",
            ),
            C(
                "Рукопожатие напрямую в процесс",
                "Если через nginx не работает, а напрямую работает, причина в nginx.",
                r"""curl -s "http://127.0.0.1:8002/socket.io/?EIO=4&transport=polling"; echo""",
            ),
            C(
                "Открытые соединения к каждому процессу",
                "Соединения от nginx: сокеты кабинета и keepalive. Сильный перекос между процессами — повод проверить `ip_hash`.",
                r"""for p in 8001 8002; do echo "$p: $(ss -Htn state established "( sport = :$p )" | wc -l)"; done""",
            ),
            C(
                "Шина realtime подключена",
                "Каждый процесс API после старта подключается к каналу. Частые `reconnect` означают проблемы с прямым подключением к Postgres.",
                r"""sudo journalctl -u 'skrezo-api@*' --no-pager | grep -E 'realtime_listener_(connected|reconnect)' | tail -6""",
                "`realtime_listener_connected` после каждого старта, без серий `reconnect`.",
                kw="listen notify шина",
            ),
        ],
    )
)

# ── Воркеры и очередь ─────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "workers",
        "Воркеры и очередь",
        "Воркеры и очередь arq",
        "Фоновые задания и расписание выполняет arq. Очередь лежит в Redis: RabbitMQ в проекте нет. "
        "Каждая cron-задача выполняется одним из двух воркеров.",
        meta=["arq", "Redis arq:queue", "worker:heartbeat", "job_run"],
        kw="rabbit rabbitmq брокер очередь",
        items=[
            C(
                "Статус воркеров",
                "Оба процесса arq, память и последние строки журнала.",
                r"""systemctl status 'skrezo-worker@*' --no-pager""",
            ),
            C(
                "Пульс воркеров",
                "Воркер раз в минуту пишет отметку времени, она живёт 3 минуты. Пустой ответ и `-2` значат, что ни один воркер не отмечался 3 минуты.",
                r"""rcli GET worker:heartbeat; rcli TTL worker:heartbeat""",
                "Время меньше минуты назад и TTL от 120 до 180.",
                kw="heartbeat живой",
            ),
            C(
                "Встроенная проверка arq",
                "Счётчики одного из воркеров: выполнено, упало, повторено, в работе, в очереди. Запись обновляется раз в час.",
                r"""skrun arq --check app.jobs.worker.WorkerSettings""",
                "`Health check successful: … j_complete=… j_failed=… queued=…`",
                kw="health check",
            ),
            C(
                "Сколько заданий в очереди",
                "Ноль или единицы. Число растёт и не убывает: воркеры стоят или не успевают.",
                r"""rcli ZCARD arq:queue""",
                kw="длина очереди backlog",
            ),
            C(
                "Что именно ждёт в очереди",
                "Идентификаторы заданий и время запуска в миллисекундах. Время в будущем у отложенного задания — это нормально.",
                r"""rcli ZRANGE arq:queue 0 20 WITHSCORES""",
            ),
            C(
                "Задания в работе прямо сейчас",
                "Ключи заданий, которые воркеры выполняют в эту секунду.",
                r"""rcli --scan --pattern 'arq:in-progress:*'""",
            ),
            C(
                "Расписание отработало",
                "`→` задание начато, `← … ●` завершено. Каждая cron-задача видна у одного из воркеров.",
                r"""sudo journalctl -u 'skrezo-worker@*' --since '1 hour ago' --no-pager | grep 'cron:'""",
                kw="cron расписание крон",
            ),
            C(
                "Упавшие и повторённые задания",
                "`!` — задание упало или исчерпало попытки, `↻` — повтор после сбоя или остановки воркера.",
                r"""sudo journalctl -u 'skrezo-worker@*' --since today --no-pager | grep -E ' ! | ↻ '""",
                "Пусто или единичные повторы.",
                kw="ошибки задания retry",
            ),
            C(
                "Журнал запусков в базе",
                "Последние запуски фоновых задач с итогом. То же видно в админ-панели Skrezo, раздел фоновых задач.",
                r"""pgsu -c "SELECT job_name, status, attempt, started_at, finished_at FROM job_run ORDER BY started_at DESC LIMIT 20;" """.strip(),
                kw="job_run журнал задач",
            ),
            C(
                "Сбои задач за сутки",
                "Какие задачи падали и сколько раз.",
                r"""pgsu -c "SELECT job_name, count(*) FROM job_run WHERE status = 'failed' AND started_at > now() - interval '1 day' GROUP BY 1 ORDER BY 2 DESC;" """.strip(),
                "`(0 rows)`",
            ),
            C(
                "Зависшие задачи",
                "Запуск в статусе `running` дольше часа: задача зависла или воркер упал посреди неё.",
                r"""pgsu -c "SELECT job_name, started_at FROM job_run WHERE status = 'running' AND started_at < now() - interval '1 hour';" """.strip(),
                "`(0 rows)`",
            ),
        ],
    )
)

# ── События и пуши ────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "events",
        "События и пуши",
        "События и пуши",
        "Изменения в клинике попадают в таблицу `event`. Диспетчер внутри воркера разбирает её по сигналу "
        "NOTIFY и раз в 30 секунд на всякий случай, раскладывает события по инбоксам и отправляет пуши.",
        meta=["event", "push_job", "FCM", "APNs"],
        items=[
            C(
                "Диспетчер событий подключён",
                "Старт диспетчера, подключение к сигналу и сбои его циклов.",
                r"""sudo journalctl -u 'skrezo-worker@*' --no-pager | grep -E 'event_(listener_connected|listener_reconnect|dispatcher_started|dispatcher_tick_failed)' | tail -8""",
                "`event_dispatcher_started` и `event_listener_connected` без `tick_failed`.",
                kw="outbox диспетчер",
            ),
            C(
                "Необработанные события",
                "Сколько событий ждут разбора и когда пришло самое старое.",
                r"""pgsu -c "SELECT count(*) AS pending, min(occurred_at) AS oldest FROM event WHERE processed_at IS NULL;" """.strip(),
                "`pending` около нуля. Старое `oldest` значит, что диспетчер стоит.",
            ),
            C(
                "События с ошибками",
                "Последние события, которые диспетчер не смог разобрать с первого раза.",
                r"""pgsu -c "SELECT id, type, attempts, left(last_error, 80) AS error FROM event WHERE last_error IS NOT NULL ORDER BY id DESC LIMIT 10;" """.strip(),
            ),
            C(
                "Пуши отправляются по-настоящему",
                "Как воркер собрал каналы доставки при старте.",
                r"""sudo journalctl -u 'skrezo-worker@*' --no-pager | grep push_transport_built | tail -2""",
                "`android=fcm ios=apns`. Значение `log` значит, что ключи не прочитались: см. «Неполадки».",
                kw="push пуш fcm apns",
            ),
            C(
                "Просроченные отложенные пуши",
                "Пуши, отложенные на тихие часы, срок которых прошёл, а отправки не было.",
                r"""pgsu -c "SELECT count(*) FROM push_job WHERE sent_at IS NULL AND canceled_at IS NULL AND send_after < now() - interval '5 minutes';" """.strip(),
                "`0`",
            ),
            C(
                "Ошибки доставки пушей",
                "Отказы FCM и APNs: устаревший токен устройства, неверный ключ и подобное.",
                r"""sudo journalctl -u 'skrezo-worker@*' --since today --no-pager | grep -E 'push_delivery_failed|push_job_send_failed'""",
            ),
        ],
    )
)

# ── Redis ─────────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "redis",
        "Redis",
        "Redis",
        "Очередь заданий arq, пульс воркеров, лимиты входа и OTP, кэш ассистента. Слушает только localhost, "
        "пароль в `REDIS_URL`. Политика `noeviction`: при нехватке памяти запись отклоняется, очередь не вытесняется.",
        meta=["redis-server", "127.0.0.1:6379", "/etc/redis/skrezo.conf", "AOF"],
        items=[
            C(
                "Служба",
                "Состояние процесса Redis.",
                r"""systemctl status redis-server --no-pager""",
            ),
            C("Отвечает", "Подключение с паролем из `.env`.", r"""rcli PING""", "`PONG`"),
            C(
                "Версия и время работы",
                "Версия сервера и сколько дней он работает без перезапуска.",
                r"""rcli INFO server | grep -E 'redis_version|uptime_in_days'""",
            ),
            C(
                "Память и политика",
                "Сколько занято, потолок и что Redis делает при его достижении.",
                r"""rcli INFO memory | grep -E 'used_memory_human|maxmemory_human|maxmemory_policy'""",
                "`maxmemory_human:2.00G` и `maxmemory_policy:noeviction`.",
                kw="память memory",
            ),
            C(
                "Подключённые клиенты",
                "API, воркеры и ваши консоли.",
                r"""rcli INFO clients | grep connected_clients""",
            ),
            C(
                "Запись на диск",
                "Журнал AOF включён и последние записи прошли без ошибок.",
                r"""rcli INFO persistence | grep -E 'aof_enabled|aof_last_write_status|rdb_last_bgsave_status'""",
                "`aof_enabled:1` и статусы `ok`.",
                kw="aof persistence сохранение",
            ),
            C("Ключей всего", "Число ключей в базе.", r"""rcli DBSIZE"""),
            C(
                "Ключи по группам",
                "Сводка по префиксам: `arq:*` очередь, `worker:heartbeat` пульс, `rl:*` лимиты входа и OTP, `ai:prefix:*` кэш ассистента, `ai:reply:lock:*` блокировки ответов.",
                r"""rcli --scan | cut -d: -f1-2 | sort | uniq -c | sort -rn | head -20""",
                kw="ключи keys префиксы",
            ),
            C(
                "Действующие настройки",
                "Значения, которые Redis применил после запуска.",
                r"""rcli CONFIG GET maxmemory maxmemory-policy appendonly""",
            ),
            C(
                "Медленные команды",
                "Последние команды, выполнявшиеся дольше порога.",
                r"""rcli SLOWLOG GET 10""",
            ),
            C(
                "Задержка в реальном времени",
                "Непрерывный замер, выход Ctrl+C. Норма — доли миллисекунды.",
                r"""rcli --latency""",
                kw="latency",
            ),
            C(
                "Самые крупные ключи",
                "Проход по всем ключам с поиском самых больших по типам.",
                r"""rcli --bigkeys""",
            ),
            C(
                "Советы по памяти",
                "Встроенная диагностика памяти Redis.",
                r"""rcli MEMORY DOCTOR""",
            ),
            C(
                "Лог Redis",
                "Старты, сохранения, предупреждения.",
                r"""sudo tail -n 50 /var/log/redis/redis-server.log""",
            ),
            C(
                "Перезапустить Redis",
                "API и воркеры переподключатся сами. Очередь сохраняется на диске благодаря AOF.",
                r"""sudo systemctl restart redis-server""",
                flags=((WARN, "простой"),),
            ),
            C(
                "Снять блокировку входа для номера",
                "Удаляет счётчики попыток входа и OTP для номера со всех IP. Замените цифры на номер без кода страны.",
                r"""for k in $(rcli --scan --pattern 'rl:*700123456*'); do echo "удаляю $k"; rcli DEL "$k"; done""",
                flags=((DANGER, "меняет данные"),),
                kw="лимит блокировка otp вход разблокировать",
            ),
        ],
    )
)

# ── PostgreSQL ────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "postgres",
        "PostgreSQL",
        "PostgreSQL 16",
        "База `skrezo`. Приложение ходит через PgBouncer, а шина realtime, будильник диспетчера и миграции "
        "подключаются напрямую. `pgsu` входит суперпользователем через сокет без пароля.",
        meta=["postgresql@16-main", "127.0.0.1:5432", "conf.d/skrezo.conf", "UTC"],
        items=[
            C(
                "Служба кластера",
                "Сам сервер базы. У обёртки `postgresql` статус `active (exited)`, это нормально.",
                r"""systemctl status postgresql@16-main --no-pager""",
            ),
            C(
                "Принимает соединения",
                "Проверка без входа в базу.",
                r"""pg_isready -h 127.0.0.1 -p 5432""",
                "`accepting connections`",
            ),
            C(
                "Консоль суперпользователя",
                "psql от `postgres`. Выход: `\\q`.",
                r"""pgsu""",
                kw="psql консоль",
            ),
            C(
                "Консоль глазами приложения",
                "Тот же путь, что у API: через PgBouncer с паролем из `.env`.",
                r"""pgapp""",
            ),
            C("Версия", "Версия PostgreSQL.", r"""pgsu -c 'SELECT version();'"""),
            C(
                "Размер базы",
                "Сколько места занимает база целиком.",
                r"""pgsu -c "SELECT pg_size_pretty(pg_database_size('skrezo'));" """.strip(),
            ),
            C(
                "Самые большие таблицы",
                "Десять таблиц вместе с индексами.",
                r"""pgsu -c "SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS size FROM pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;" """.strip(),
            ),
            C(
                "Соединения по состояниям",
                "Кто и в каком состоянии подключён к базе.",
                r"""pgsu -c "SELECT usename, state, count(*) FROM pg_stat_activity WHERE datname = 'skrezo' GROUP BY 1, 2 ORDER BY 3 DESC;" """.strip(),
                kw="подключения connections",
            ),
            C(
                "Долгие запросы",
                "Запросы, которые выполняются дольше 30 секунд.",
                r"""pgsu -c "SELECT pid, now() - query_start AS duration, state, left(query, 80) AS query FROM pg_stat_activity WHERE datname = 'skrezo' AND state <> 'idle' AND now() - query_start > interval '30 seconds' ORDER BY duration DESC;" """.strip(),
                "`(0 rows)`",
                kw="медленные",
            ),
            C(
                "Зависшие транзакции",
                "Транзакция открыта и ничего не делает, но держит блокировки. Через 10 минут сервер сам обрывает такие.",
                r"""pgsu -c "SELECT pid, now() - xact_start AS age, left(query, 80) AS query FROM pg_stat_activity WHERE state = 'idle in transaction' ORDER BY age DESC;" """.strip(),
                "`(0 rows)`",
            ),
            C(
                "Кто кого блокирует",
                "Запросы, которые ждут чужую блокировку, и кто её держит.",
                r"""pgsu -c "SELECT pid, pg_blocking_pids(pid) AS blocked_by, now() - query_start AS waiting, left(query, 60) AS query FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0;" """.strip(),
                "`(0 rows)`",
                kw="locks блокировки",
            ),
            C(
                "Отменить запрос",
                "Подставьте pid из запросов выше. Отменяется только текущий запрос, соединение остаётся.",
                r"""pgsu -c 'SELECT pg_cancel_backend(12345);'""",
                flags=((WARN, "осторожно"),),
            ),
            C(
                "Оборвать соединение",
                "Жёстче: закрывает соединение целиком, его транзакция откатывается.",
                r"""pgsu -c 'SELECT pg_terminate_backend(12345);'""",
                flags=((DANGER, "откат транзакции"),),
            ),
            C(
                "Расширения",
                "Расширения, которые нужны миграциям.",
                r"""pgsu -c '\dx'""",
                "`pg_trgm` и `btree_gist` в списке.",
            ),
            C(
                "Ключевые настройки",
                "Значения, с которыми работает сервер.",
                r"""pgsu -c "SELECT name, setting, unit FROM pg_settings WHERE name IN ('shared_buffers', 'effective_cache_size', 'work_mem', 'max_connections', 'timezone', 'idle_in_transaction_session_timeout');" """.strip(),
            ),
            C(
                "Медленные запросы из лога",
                "В лог попадают запросы дольше 500 мс.",
                r"""sudo grep 'duration:' /var/log/postgresql/postgresql-16-main.log | tail -20""",
            ),
            C(
                "Мусор в таблицах",
                "Мёртвые строки и когда таблицу последний раз чистил автовакуум.",
                r"""pgsu -c "SELECT relname, n_live_tup, n_dead_tup, last_autovacuum FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 10;" """.strip(),
                kw="vacuum автовакуум",
            ),
            C(
                "Перечитать настройки без перезапуска",
                "Подходит для большинства параметров. `shared_buffers` и `max_connections` требуют перезапуска.",
                r"""pgsu -c 'SELECT pg_reload_conf();'""",
            ),
            C(
                "Перезапустить PostgreSQL",
                "Все соединения обрываются, сайт недоступен несколько секунд. Приложение переподключится само.",
                r"""sudo systemctl restart postgresql@16-main""",
                flags=((WARN, "простой"),),
            ),
        ],
    )
)

# ── PgBouncer ─────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "pgbouncer",
        "PgBouncer",
        "PgBouncer",
        "Пул соединений между приложением и базой, режим transaction. `pgb` открывает его консоль "
        "статистики паролем приложения.",
        meta=[
            "pgbouncer",
            "127.0.0.1:6432",
            "pool_mode=transaction",
            "max_prepared_statements=500",
        ],
        items=[
            C("Служба", "Состояние процесса пулера.", r"""systemctl status pgbouncer --no-pager"""),
            C(
                "Принимает соединения",
                "Проверка без входа.",
                r"""pg_isready -h 127.0.0.1 -p 6432 -d skrezo -U skrezo""",
                "`accepting connections`",
            ),
            C(
                "Пулы",
                "`cl_active` — клиенты в работе, `cl_waiting` — ждут свободное соединение, `sv_active` и `sv_idle` — соединения пулера с базой.",
                r"""pgb -c 'SHOW POOLS;'""",
                "`cl_waiting` равно 0.",
                kw="pool пул",
            ),
            C(
                "Статистика",
                "Транзакции и запросы в секунду, среднее время запроса.",
                r"""pgb -c 'SHOW STATS;'""",
            ),
            C("Клиенты", "Кто подключён к пулеру.", r"""pgb -c 'SHOW CLIENTS;'"""),
            C(
                "Соединения с базой",
                "Соединения пулера с PostgreSQL.",
                r"""pgb -c 'SHOW SERVERS;'""",
            ),
            C(
                "Режим и подготовленные запросы",
                "Без `max_prepared_statements` драйвер asyncpg ловит ошибки `prepared statement does not exist`.",
                r"""pgb -c 'SHOW CONFIG;' | grep -E 'pool_mode|max_prepared_statements|default_pool_size|max_client_conn'""",
                "`transaction`, `500`, `40`, `500`.",
                kw="prepared statement",
            ),
            C(
                "Перечитать конфиг",
                "Применяет правки `pgbouncer.ini` без обрыва соединений.",
                r"""sudo systemctl reload pgbouncer""",
            ),
            C(
                "Лог пулера",
                "Ошибки входа, переполнение пула.",
                r"""sudo tail -n 50 /var/log/postgresql/pgbouncer.log""",
            ),
            C(
                "Обновить пароль приложения в пулере",
                "Нужно после смены пароля роли `skrezo` в Postgres, иначе пулер не пустит приложение. Пароль в открытом виде нигде не появляется.",
                r"""(cd /tmp && sudo -u postgres psql -Atc "SELECT concat('\"', usename, '\" \"', passwd, '\"') FROM pg_shadow WHERE usename = 'skrezo'") | sudo tee /etc/pgbouncer/userlist.txt > /dev/null && sudo systemctl reload pgbouncer""",
                flags=((WARN, "после смены пароля"),),
                kw="userlist пароль scram",
            ),
        ],
    )
)

# ── nginx ─────────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "nginx",
        "nginx и HTTPS",
        "nginx и HTTPS",
        "Принимает HTTPS, раздаёт запросы двум процессам API с `ip_hash`, пропускает WebSocket. "
        "Сертификат Let's Encrypt продлевается таймером certbot.",
        meta=["nginx", ":80 → :443", "sites-available/skrezo", "certbot.timer"],
        items=[
            C(
                "Проверить конфиг",
                "Синтаксис и пути всех файлов конфигурации.",
                r"""sudo nginx -t""",
                "`syntax is ok` и `test is successful`.",
            ),
            C(
                "Применить изменения без простоя",
                "Проверка, затем мягкая перезагрузка конфигурации.",
                r"""sudo nginx -t && sudo systemctl reload nginx""",
            ),
            C("Служба", "Состояние nginx.", r"""systemctl status nginx --no-pager"""),
            C(
                "Ошибки nginx",
                "`connect() failed … 127.0.0.1:8001` значит, что процесс API не отвечал.",
                r"""sudo tail -n 50 /var/log/nginx/error.log""",
                kw="502 ошибки",
            ),
            C(
                "Поток запросов",
                "Живой журнал запросов. Выход: Ctrl+C.",
                r"""sudo tail -f /var/log/nginx/access.log""",
            ),
            C(
                "Ответы с кодом 5xx",
                "Последние запросы, на которые сервер ответил ошибкой.",
                r"""sudo awk '$9 ~ /^5/' /var/log/nginx/access.log | tail -20""",
                "Пусто.",
                kw="500 502 504",
            ),
            C(
                "Самые активные IP",
                "Кто присылает больше всего запросов.",
                r"""sudo awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head""",
            ),
            C(
                "DNS смотрит на сервер",
                "Адрес домена и собственный адрес сервера.",
                r"""getent ahostsv4 {{DOMAIN}} | head -1; hostname -I | awk '{print $1}'""",
                "Два одинаковых адреса. Разные: DNS не обновился или домен за прокси Cloudflare.",
                kw="dns домен",
            ),
            C(
                "Сертификаты и сроки",
                "Какие сертификаты выпущены и сколько дней им осталось.",
                r"""sudo certbot certificates""",
                kw="ssl tls сертификат",
            ),
            C(
                "Проверка автопродления",
                "Пробное продление без замены настоящего сертификата.",
                r"""sudo certbot renew --dry-run""",
                "`all simulated renewals succeeded`",
            ),
            C(
                "Срок сертификата снаружи",
                "Даты сертификата, который видят клиенты.",
                r"""echo | openssl s_client -connect {{DOMAIN}}:443 -servername {{DOMAIN}} 2>/dev/null | openssl x509 -noout -dates""",
            ),
        ],
    )
)

# ── Интеграции ────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "integrations",
        "WhatsApp и ассистент",
        "WhatsApp и ассистент",
        "Вебхук Meta, проверки ассистента по расписанию и предохранитель: после серии сбоев провайдера ИИ "
        "бот перестаёт отвечать сам и передаёт диалоги людям.",
        meta=["/api/v1/whatsapp/webhook", "ai:provider:open"],
        items=[
            C(
                "Вебхук принимает токен Meta",
                "Тот же запрос, которым Meta подтверждает подписку. Идёт напрямую в процесс, чтобы токен не попал в журнал nginx.",
                r"""curl -s "http://127.0.0.1:8001/api/v1/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=$(skenv WA_CLOUD_VERIFY_TOKEN)&hub.challenge=ok123"; echo""",
                "`ok123`. Ошибка 403 значит, что токен в `.env` не совпадает с указанным в Meta.",
                kw="meta webhook вебхук",
            ),
            C(
                "Вебхук доступен из интернета",
                "Запрос снаружи с заведомо неверным токеном: адрес должен ответить отказом.",
                r"""curl -s -o /dev/null -w '%{http_code}\n' "https://{{DOMAIN}}/api/v1/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=x" """.strip(),
                "`403`. Код 404 или 502 значит, что адрес снаружи не доходит до API.",
            ),
            C(
                "Проверки по расписанию",
                "Здоровье ассистента раз в 5 минут, токены номеров WhatsApp раз в час.",
                r"""sudo journalctl -u 'skrezo-worker@*' --since today --no-pager | grep -E 'cron:check_(ai_health|whatsapp_tokens)' | tail -6""",
            ),
            C(
                "Предохранитель ассистента",
                "`1` — провайдер ИИ считается недоступным, бот передаёт диалоги людям. Второе число — сбои подряд.",
                r"""rcli EXISTS ai:provider:open; rcli GET ai:provider:failures""",
                "`0` и пустой ответ.",
                kw="gemini ии бот молчит",
            ),
            C(
                "Сбросить предохранитель",
                "Только когда провайдер снова работает, иначе предохранитель откроется заново.",
                r"""rcli DEL ai:provider:open ai:provider:failures""",
                flags=((WARN, "осторожно"),),
            ),
            C(
                "Как подключены номера клиник",
                "Номер, режим, кто подключал (клиника сама или Skrezo), включён ли и сколько истории перенесено.",
                r"""pgsu -c "SELECT c.name, w.display_phone, w.mode, w.connected_via, w.active, w.history_progress FROM clinic_whatsapp w JOIN clinic c ON c.id = w.clinic_id ORDER BY c.name;" """.strip(),
                kw="embedded signup подключение номер",
            ),
            C(
                "Номера, которые нужно переподключить",
                "Доступ отозван, номер перерегистрирован на другом телефоне или Meta не принимает токен клиники. Команда Skrezo получает о каждом случае одно сообщение в чат.",
                r"""pgsu -c "SELECT c.name, w.display_phone, w.needs_reconnect_reason, w.needs_reconnect_at, w.reconnect_notified_at FROM clinic_whatsapp w JOIN clinic c ON c.id = w.clinic_id WHERE w.needs_reconnect_at IS NOT NULL;" """.strip(),
                "`(0 rows)`",
                kw="переподключить отозван доступ",
            ),
            C(
                "Итоги ежечасной проверки номеров",
                "Сколько токенов отвергнуто, сколько номеров помечено и снято, сколько синхронизаций дозапрошено.",
                r"""pgsu -c "SELECT started_at, status, detail FROM job_run WHERE job_name = 'check_whatsapp_tokens' ORDER BY started_at DESC LIMIT 5;" """.strip(),
            ),
            C(
                "Кэш контекста ассистента",
                "Для скольких клиник сейчас закэширован контекст ассистента у провайдера.",
                r"""rcli --scan --pattern 'ai:prefix:*' | wc -l""",
                kw="кэш prefix gemini",
            ),
        ],
    )
)

# ── Настройки ─────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "settings",
        "Настройки",
        "Настройки",
        "Где лежит каждая настройка и как её поменять. Конфиги в `/etc` скопированы из каталога `deploy/` "
        "репозитория: правку на сервере стоит повторить там, иначе она потеряется при переустановке.",
        meta=["/opt/skrezo/app/.env", "deploy/"],
        static="SETTINGS_TABLE",
        items=[
            C(
                "Посмотреть .env без секретов",
                "Все заданные переменные. Пароли, токены, ключи и адреса с паролями заменены точками.",
                r"""sudo sed -E '/^[[:space:]]*(#|$)/d; s/^([A-Z0-9_]*(SECRET|TOKEN|PASSWORD|KEY|DATABASE_URL|DATABASE_DIRECT_URL|REDIS_URL)[A-Z0-9_]*)=.*/\1=•••/' /opt/skrezo/app/.env""",
                kw="env переменные окружения",
            ),
            C(
                "Что видит приложение",
                "Значения, которые приложение реально прочитало: окружение, адреса, пуши, хранилище.",
                r"""skrun python -c 'from app.core.config import settings as s; print("env:", s.app_env); print("api:", s.public_api_url); print("cabinet:", s.cabinet_url); print("root_path:", s.root_path or "-"); print("push:", s.push_enabled); print("storage:", s.storage_root)'""",
                "`env: production` и ваши адреса.",
            ),
            C(
                "Изменить .env",
                "Редактор от имени `skrezo`: владелец и права файла не меняются. Затем перезапустите API без простоя и воркеры.",
                r"""sudo -u skrezo nano /opt/skrezo/app/.env""",
                flags=((WARN, "нужен перезапуск"),),
            ),
            C(
                "Изменить конфиг nginx",
                "После сохранения: `sudo nginx -t && sudo systemctl reload nginx`.",
                r"""sudo nano /etc/nginx/sites-available/skrezo""",
            ),
            C(
                "Изменить конфиг PgBouncer",
                "После сохранения: `sudo systemctl reload pgbouncer`.",
                r"""sudo nano /etc/pgbouncer/pgbouncer.ini""",
            ),
            C(
                "Изменить конфиг Redis",
                "После сохранения: `sudo systemctl restart redis-server`.",
                r"""sudo nano /etc/redis/skrezo.conf""",
            ),
            C(
                "Изменить конфиг PostgreSQL",
                "После сохранения: `pgsu -c 'SELECT pg_reload_conf();'`, а для памяти и числа соединений перезапуск.",
                r"""sudo nano /etc/postgresql/16/main/conf.d/skrezo.conf""",
            ),
        ],
    )
)

# ── Деплой и миграции ─────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "deploy",
        "Деплой и миграции",
        "Деплой, миграции, администратор",
        "Код живёт в `/opt/skrezo/app` и принадлежит пользователю `skrezo`. Git, alembic и python запускаются "
        "от его имени: через `skrun` одной командой или в оболочке `skr`.",
        meta=["/opt/skrezo/app", "alembic", "uv"],
        items=[
            C(
                "Оболочка проекта",
                "Shell от `skrezo` в каталоге проекта с активным окружением: git, alembic, python работают как есть. Выход: `exit`.",
                r"""skr""",
            ),
            C(
                "Текущая миграция",
                "Ревизия схемы базы, на которой стоит прод.",
                r"""skrun alembic current""",
                "Ревизия с пометкой `(head)`.",
                kw="миграция ревизия",
            ),
            C(
                "Последние миграции",
                "Десять последних ревизий с описаниями.",
                r"""skrun alembic history | head -n 10""",
            ),
            C(
                "Применить миграции вручную",
                "Обычно это делает `deploy.sh`.",
                r"""skrun alembic upgrade head""",
            ),
            C(
                "Откатить последнюю миграцию",
                "Только если релиз этого требует: откат может удалить колонки вместе с данными. Затем `deploy.sh` на предыдущую версию.",
                r"""skrun alembic downgrade -1""",
                flags=((DANGER, "меняет данные"),),
            ),
            C(
                "Создать или обновить админа Skrezo",
                "Выполнять внутри `skr`. Пробел в начале строки не даёт паролю попасть в историю команд.",
                r""" python -m app.scripts.create_platform_user --phone 0700000001 --password 'СИЛЬНЫЙ_ПАРОЛЬ' --full-name 'Имя Фамилия'""",
                kw="админ администратор пароль",
            ),
            C(
                "Обновить зависимости вручную",
                "Обычно это делает `deploy.sh`.",
                r"""skrun uv sync --frozen --no-dev --compile-bytecode""",
                kw="uv пакеты",
            ),
            C(
                "Подтянуть код без перезапуска",
                "Файлы обновятся, а процессы продолжат работать на старой версии до перезапуска.",
                r"""sudo -u skrezo -H git -C /opt/skrezo/app pull --ff-only""",
            ),
        ],
    )
)

# ── Бэкапы ────────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "backups",
        "Бэкапы",
        "Бэкапы",
        "Каждую ночь в 01:30 UTC: дамп базы и зеркало файлов пациентов в `/var/backups/skrezo`, дампы "
        "хранятся 14 дней. Копия лежит на том же диске, её стоит забирать на другой хост.",
        meta=["skrezo-backup.timer", "/var/backups/skrezo", "14 дней"],
        items=[
            C(
                "Сделать бэкап сейчас",
                "Тот же скрипт, что запускает таймер.",
                r"""sudo /opt/skrezo/app/deploy/bin/backup.sh""",
                "`backup ok: /var/backups/skrezo/db/skrezo-….dump`",
                kw="бэкап дамп",
            ),
            C(
                "Список дампов",
                "Файлы дампов с размерами и датами.",
                r"""sudo ls -lh /var/backups/skrezo/db/""",
            ),
            C(
                "Когда следующий",
                "Ближайший и прошлый запуск таймера.",
                r"""systemctl list-timers skrezo-backup.timer --no-pager""",
            ),
            C(
                "Как прошёл последний",
                "Вывод скрипта бэкапа.",
                r"""sudo journalctl -u skrezo-backup -n 20 --no-pager""",
            ),
            C(
                "Свежий дамп читается",
                "Оглавление последнего дампа. Ошибка значит, что файл повреждён.",
                r"""sudo sh -c 'pg_restore --list "$(ls -t /var/backups/skrezo/db/*.dump | head -1)" | head -20'""",
            ),
            C(
                "Размер бэкапов",
                "Сколько занимают дампы и копия файлов.",
                r"""sudo du -sh /var/backups/skrezo/db /var/backups/skrezo/storage""",
            ),
            C(
                "Восстановить базу из дампа",
                "Останавливает приложение, заменяет содержимое базы дампом и запускает снова. Всё, что записано после дампа, пропадёт. Подставьте имя файла из списка дампов.",
                r"""sudo systemctl stop skrezo.target
sudo install -o postgres -m 600 /var/backups/skrezo/db/skrezo-ДАТА.dump /tmp/restore.dump
(cd /tmp && sudo -u postgres pg_restore --clean --if-exists --no-owner --role=skrezo --dbname=skrezo /tmp/restore.dump)
sudo rm /tmp/restore.dump
sudo systemctl start skrezo.target""",
                flags=((DANGER, "затирает базу"), (WARN, "простой")),
                kw="restore восстановление",
            ),
            C(
                "Вернуть файлы пациентов из копии",
                "Докладывает недостающие файлы из ночной копии. Новые файлы не удаляются.",
                r"""sudo rsync -a /var/backups/skrezo/storage/ /opt/skrezo/app/storage/ && sudo chown -R skrezo:skrezo /opt/skrezo/app/storage""",
                flags=((WARN, "меняет файлы"),),
                kw="storage файлы восстановить",
            ),
        ],
    )
)

# ── Система ───────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "system",
        "Система",
        "Система и безопасность",
        "Ресурсы сервера, порты, файрвол, fail2ban, обновления и перезагрузка.",
        meta=["Ubuntu 24.04", "12 CPU", "47 ГБ", "ufw", "fail2ban"],
        items=[
            C(
                "Нагрузка",
                "Средняя загрузка за 1, 5 и 15 минут. Для 12 ядер тревожно, когда она долго выше 12.",
                r"""uptime""",
                kw="load cpu процессор",
            ),
            C("Память", "Занято, свободно и доступно с учётом кэша.", r"""free -h"""),
            C("Диск", "Свободное место на корневом разделе.", r"""df -h /""", kw="место диск"),
            C(
                "Что занимает место",
                "Файлы пациентов, бэкапы, база и логи.",
                r"""sudo du -sh /opt/skrezo/app/storage /var/backups/skrezo /var/lib/postgresql /var/log 2>/dev/null""",
            ),
            C(
                "Процессы по памяти",
                "Пятнадцать самых больших процессов.",
                r"""ps aux --sort=-%mem | head -15""",
            ),
            C(
                "Открытые порты",
                "Какие процессы и на каких адресах слушают.",
                r"""sudo ss -tlnp""",
                "Наружу, на `0.0.0.0` и `[::]`, только 22, 80 и 443. Остальное на `127.0.0.1`.",
                kw="порты ports",
            ),
            C(
                "Файрвол",
                "Правила ufw.",
                r"""sudo ufw status verbose""",
                "Разрешены OpenSSH и Nginx Full.",
            ),
            C(
                "Баны fail2ban",
                "Сколько IP заблокировано за перебор паролей SSH.",
                r"""sudo fail2ban-client status sshd""",
            ),
            C(
                "Разбанить IP",
                "Если заблокирован свой адрес. Подставьте IP.",
                r"""sudo fail2ban-client set sshd unbanip 203.0.113.10""",
                kw="бан unban",
            ),
            C(
                "Время и часовой пояс",
                "Сервер живёт по UTC, часы синхронизированы.",
                r"""timedatectl""",
                "`Time zone: Etc/UTC` и `System clock synchronized: yes`.",
            ),
            C(
                "Доступные обновления",
                "Обновить список пакетов и показать, что можно обновить.",
                r"""sudo apt update && apt list --upgradable""",
            ),
            C(
                "Нужна ли перезагрузка",
                "Ubuntu отмечает, когда обновления требуют перезагрузки.",
                r"""cat /var/run/reboot-required 2>/dev/null || echo "перезагрузка не нужна" """.strip(),
            ),
            C(
                "После перезагрузки всё поднялось",
                "Все службы, от которых зависит Skrezo.",
                r"""systemctl is-active nginx pgbouncer redis-server postgresql@16-main skrezo-api@8001 skrezo-api@8002 skrezo-worker@1 skrezo-worker@2""",
                "`active` в каждой строке.",
                kw="ребут reboot",
            ),
            C(
                "Перезагрузить сервер",
                "Все сервисы поднимутся сами через минуту-две.",
                r"""sudo reboot""",
                flags=((WARN, "простой"),),
            ),
        ],
    )
)

# ── Масштабирование ───────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "scale",
        "Масштабирование",
        "Масштабирование",
        "Новый процесс API или воркер — ещё один экземпляр того же шаблона. Порт или номер нужно также "
        "дописать в `API_PORTS` или `WORKERS` в `deploy/bin/deploy.sh` репозитория, иначе деплой его не перезапустит.",
        meta=["skrezo-api@8003", "skrezo-worker@3"],
        items=[
            C(
                "Третий процесс API",
                "Запускает процесс на 8003, включает его автозапуск и добавляет в upstream nginx.",
                r"""sudo systemctl enable --now skrezo-api@8003
sudo sed -i 's/server 127.0.0.1:8002;/server 127.0.0.1:8002;\n    server 127.0.0.1:8003;/' /etc/nginx/sites-available/skrezo
sudo nginx -t && sudo systemctl reload nginx""",
                kw="добавить процесс api",
            ),
            C(
                "Третий воркер",
                "Запускает ещё один воркер и включает его автозапуск.",
                r"""sudo systemctl enable --now skrezo-worker@3""",
            ),
            C(
                "Хватает ли соединений с базой",
                "Каждый процесс держит до 30 соединений с пулером. `cl_waiting` больше нуля: пулу не хватает соединений с базой.",
                r"""pgb -c 'SHOW POOLS;'""",
            ),
        ],
    )
)

# ── Неполадки ─────────────────────────────────────────────────────────────
SECTIONS.append(
    S(
        "trouble",
        "Неполадки",
        "Неполадки",
        "Симптом, причина и что выполнить.",
        meta=[],
        items=[
            C(
                "nginx отвечает 502",
                "Процесс API не отвечает: упал, перезапускается или завис. Статус покажет, какой именно.",
                r"""systemctl status 'skrezo-api@*' --no-pager; sudo tail -n 20 /var/log/nginx/error.log""",
                kw="502 bad gateway",
            ),
            C(
                "Кабинет то и дело теряет связь в реальном времени",
                "Запросы одного клиента должны попадать в один процесс: в upstream нужен `ip_hash`. За прокси Cloudflare нужен ещё файл `cloudflare-realip.conf`.",
                r"""grep -n ip_hash /etc/nginx/sites-available/skrezo; ls /etc/nginx/conf.d/""",
                kw="сокет переподключение session id unknown",
            ),
            C(
                "Ошибки «prepared statement … does not exist»",
                "У PgBouncer выключено отслеживание подготовленных запросов.",
                r"""pgb -c 'SHOW CONFIG;' | grep max_prepared_statements""",
                "`500`. Значение `0`: впишите `max_prepared_statements = 500` в `pgbouncer.ini` и перечитайте конфиг.",
            ),
            C(
                "Пуши пишутся в журнал, а не уходят",
                "Воркер не может прочитать ключи в `secrets`: обычно после переноса файлов через `mv` владельцем остался другой пользователь.",
                r"""sudo chown -R skrezo:skrezo /opt/skrezo/app/secrets && sudo chmod 700 /opt/skrezo/app/secrets && sudo chmod 600 /opt/skrezo/app/secrets/* && sudo systemctl restart skrezo-worker@1 skrezo-worker@2""",
                "После перезапуска `push_transport_built android=fcm ios=apns`.",
                kw="push пуши log permission denied",
            ),
            C(
                "Фоновые задания не выполняются",
                "Пульс, длина очереди и состояние воркеров в одной команде.",
                r"""rcli GET worker:heartbeat; rcli ZCARD arq:queue; systemctl is-active skrezo-worker@1 skrezo-worker@2""",
                kw="задания очередь стоит",
            ),
            C(
                "Бот перестал отвечать, всё уходит людям",
                "Скорее всего открыт предохранитель провайдера ИИ. Причину ищите в журнале воркеров.",
                r"""rcli EXISTS ai:provider:open; sudo journalctl -u 'skrezo-worker@*' --since '1 hour ago' --no-pager | grep -Ei 'ai_|gemini' | tail -20""",
                kw="ассистент ии бот gemini",
            ),
            C(
                "Диск почти заполнен",
                "Обычно растут журналы, бэкапы или файлы пациентов.",
                r"""df -h /; sudo du -sh /var/log /var/backups/skrezo /opt/skrezo/app/storage; journalctl --disk-usage""",
            ),
            C(
                "Не пускает по SSH после неудачных попыток",
                "Адрес попал в бан fail2ban на сутки. Войдите через консоль в панели хостинга и снимите бан.",
                r"""sudo fail2ban-client set sshd unbanip ВАШ_IP""",
                kw="ssh бан вход",
            ),
            C(
                "Деплой останавливается на git checkout",
                "На сервере правили файлы кода. Команда убирает правки в сторону, их можно вернуть через `git stash pop`.",
                r"""sudo -u skrezo -H git -C /opt/skrezo/app stash""",
                flags=((WARN, "убирает правки"),),
            ),
            C(
                "Сервис не стартует после правки .env",
                "Ошибка настройки видна в первых строках журнала: pydantic называет переменную и причину.",
                r"""sudo journalctl -u skrezo-api@8001 -n 60 --no-pager | grep -iE -A3 'validation|error'""",
                kw="env настройки старт",
            ),
        ],
    )
)
