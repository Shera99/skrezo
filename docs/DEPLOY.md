# Развёртывание на сервере без Docker (Ubuntu 24.04 + systemd)

Пошаговая установка прода на «голый» сервер: всё ставится пакетами и
запускается юнитами systemd, чтобы после перезагрузки или падения сервера
сервисы поднялись сами. Готовые конфиги лежат в каталоге [`deploy/`](../deploy/)
репозитория — команды ниже копируют их на место.

Проверено под сервер **12 CPU / 47 GB RAM / 385 GB SSD**, Ubuntu 24.04 LTS.

## Что получится

```
интернет ──HTTPS──▶ nginx :443 (TLS, ip_hash) ──┬─▶ uvicorn 127.0.0.1:8001   skrezo-api@8001
                                                 └─▶ uvicorn 127.0.0.1:8002   skrezo-api@8002
                       arq-воркеры (очередь + cron'ы)     skrezo-worker@1, skrezo-worker@2
PgBouncer 127.0.0.1:6432 (transaction) ──▶ PostgreSQL 16 127.0.0.1:5432
Redis 127.0.0.1:6379  — очередь arq, OTP, лимиты, кэш префикса Gemini
/opt/skrezo/app/storage — снимки и документы пациентов;  /opt/skrezo/app/secrets — ключи пушей
```

- **Два инстанса API** на разных портах вместо `uvicorn --workers`: nginx через
  `ip_hash` держит клиента Socket.IO на одном процессе (long-polling → websocket),
  а деплой перезапускает инстансы по одному — без простоя. Между процессами
  события идут через шину `pg_notify`, комнаты сокета не теряются.
- **Два воркера**: cron'ы arq ставятся по `job_id` в общем Redis (выполняет
  один), диспетчер событий берёт строки `SKIP LOCKED`, хранилище общее.
- **PgBouncer в transaction-режиме** с `max_prepared_statements` (asyncpg готовит
  запросы на уровне протокола). `LISTEN` через такой пулер не работает, поэтому
  слушатели `pg_notify` (сокет, диспетчер) и миграции Alembic ходят по
  `DATABASE_DIRECT_URL` — прямому порту Postgres.
- **RabbitMQ не нужен**: очередь проекта — arq поверх Redis. Docker тоже не нужен.

| Файл в репозитории | Куда на сервере |
|---|---|
| `deploy/systemd/skrezo-api@.service` | `/etc/systemd/system/` — шаблон API, инстанс = порт |
| `deploy/systemd/skrezo-worker@.service` | `/etc/systemd/system/` — шаблон воркера |
| `deploy/systemd/skrezo.target` | `/etc/systemd/system/` — группа «всё Skrezo» |
| `deploy/systemd/skrezo-backup.{service,timer}` | `/etc/systemd/system/` — ночной бэкап |
| `deploy/nginx/skrezo.conf` | `/etc/nginx/sites-available/skrezo` |
| `deploy/pgbouncer/pgbouncer.ini` | `/etc/pgbouncer/pgbouncer.ini` |
| `deploy/postgres/skrezo.conf` | `/etc/postgresql/16/main/conf.d/skrezo.conf` |
| `deploy/redis/skrezo.conf` | `/etc/redis/skrezo.conf` (include в конце `redis.conf`) |
| `deploy/sysctl/99-skrezo.conf` | `/etc/sysctl.d/99-skrezo.conf` |
| `deploy/bin/deploy.sh` | запускается из репозитория — деплой без простоя |
| `deploy/bin/backup.sh` | запускается из репозитория — таймером бэкапа |

## 0. Что нужно заранее

- Домен API (A-запись на IP сервера; в примерах `api.skrezo.com`) и домен кабинета
  (`CABINET_URL`, только https и без `www`).
- Прод-`.env` и каталог `secrets/` (ключи APNs/FCM) со старого сервера.
- Доступ к старому серверу для переноса базы и `storage/` (раздел 8).

Все команды — от **root**: войдя под своим пользователем с sudo, один раз выполните
`sudo -i` и дальше работайте в этой сессии. Иначе `apt`, `cp` в `/etc`, `systemctl` и
установщик uv упадут с «Permission denied»; конвейер `curl … | sh` с одним `sudo`
перед `curl` не спасает — права нужны второй половине.

## 1. Система

```bash
apt update && apt -y upgrade
timedatectl set-timezone UTC          # cron'ы воркера считают в UTC («03:12 UTC = 09:12 Бишкек»)
locale-gen en_US.UTF-8                # коллация базы — как в Docker-образе postgres:16
apt -y install git curl ca-certificates build-essential rsync ufw fail2ban unattended-upgrades \
    postgresql-16 postgresql-contrib pgbouncer redis-server nginx certbot python3-certbot-nginx
```

Файрвол — наружу только SSH и nginx (Postgres, PgBouncer, Redis и uvicorn слушают
только `127.0.0.1`):

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
systemctl enable --now fail2ban       # jail sshd включён по умолчанию
```

Журнал systemd — ограничить размер, иначе логи за год съедят диск:

```bash
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=2G\n' > /etc/systemd/journald.conf.d/skrezo.conf
systemctl restart systemd-journald
```

## 2. Пользователь, uv и код

```bash
useradd --system --create-home --home-dir /opt/skrezo --shell /bin/bash skrezo
chmod 750 /opt/skrezo

# Deploy-ключ GitHub (только чтение): публичную часть — в репозиторий,
# Settings → Deploy keys.
sudo -u skrezo -H bash -c 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
    ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C skrezo-prod && \
    ssh-keyscan github.com >> ~/.ssh/known_hosts'
cat /opt/skrezo/.ssh/id_ed25519.pub

sudo -u skrezo -H git clone git@github.com:Shera99/skrezo.git /opt/skrezo/app
```

uv ставится в `/usr/local/bin`; Python 3.14 (в Ubuntu 24.04 его нет) uv скачает
сам в `/opt/skrezo/.local/share/uv/python` — версия зафиксирована в
`.python-version`, зависимости — в `uv.lock`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
uv --version
sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && uv python install 3.14 && uv sync --frozen --no-dev'
/opt/skrezo/app/.venv/bin/python --version      # Python 3.14.x

cp /opt/skrezo/app/deploy/sysctl/99-skrezo.conf /etc/sysctl.d/99-skrezo.conf
sysctl --system
```

## 3. PostgreSQL 16

```bash
cp /opt/skrezo/app/deploy/postgres/skrezo.conf /etc/postgresql/16/main/conf.d/skrezo.conf
systemctl restart postgresql

PG_PASS=$(openssl rand -hex 24); echo "PG_PASS=$PG_PASS"      # запишите — пойдёт в .env
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE skrezo LOGIN PASSWORD '$PG_PASS';
CREATE DATABASE skrezo OWNER skrezo ENCODING 'UTF8'
    LC_COLLATE 'en_US.UTF-8' LC_CTYPE 'en_US.UTF-8' TEMPLATE template0;
\c skrezo
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;
SQL
```

Расширения нужны миграциям (`pg_trgm` — склейка пробелов знаний, `btree_gist` —
запрет пересечения приёмов); они «доверенные», владелец базы создал бы их сам, но
заранее — надёжнее.

## 4. PgBouncer

```bash
cp /opt/skrezo/app/deploy/pgbouncer/pgbouncer.ini /etc/pgbouncer/pgbouncer.ini
# SCRAM-секрет роли — из pg_shadow: пароль в открытом виде нигде не лежит.
sudo -u postgres psql -Atc "SELECT concat('\"', usename, '\" \"', passwd, '\"') FROM pg_shadow WHERE usename = 'skrezo'" \
    > /etc/pgbouncer/userlist.txt
chown postgres:postgres /etc/pgbouncer/userlist.txt && chmod 640 /etc/pgbouncer/userlist.txt
systemctl enable --now pgbouncer && systemctl restart pgbouncer

psql "postgresql://skrezo:$PG_PASS@127.0.0.1:6432/skrezo" -c 'SELECT 1'     # через пулер
psql "postgresql://skrezo:$PG_PASS@127.0.0.1:5432/skrezo" -c 'SELECT 1'     # напрямую
```

## 5. Redis

```bash
REDIS_PASS=$(openssl rand -hex 24); echo "REDIS_PASS=$REDIS_PASS"   # запишите — пойдёт в .env
cp /opt/skrezo/app/deploy/redis/skrezo.conf /etc/redis/skrezo.conf
sed -i "s/ЗАМЕНИТЬ_ПАРОЛЬ_REDIS/$REDIS_PASS/" /etc/redis/skrezo.conf
chown redis:redis /etc/redis/skrezo.conf && chmod 640 /etc/redis/skrezo.conf
echo 'include /etc/redis/skrezo.conf' >> /etc/redis/redis.conf      # именно в конец
systemctl enable --now redis-server && systemctl restart redis-server
redis-cli -a "$REDIS_PASS" --no-auth-warning ping                    # PONG
```

`maxmemory-policy noeviction` намеренно: в Redis лежит очередь заданий, и
вытеснение «лишних» ключей потеряло бы ответы ассистента и вебхуки.

## 6. `.env` и ключи

```bash
# Со старого сервера (или из .env.example, если с нуля):
scp root@СТАРЫЙ:/путь/к/проекту/.env /opt/skrezo/app/.env
scp -r root@СТАРЫЙ:/путь/к/проекту/secrets/ /opt/skrezo/app/
chown -R skrezo:skrezo /opt/skrezo/app/.env /opt/skrezo/app/secrets
chmod 600 /opt/skrezo/app/.env && chmod 700 /opt/skrezo/app/secrets
```

Что меняется в `.env` под этот сервер (остальное — как на старом проде):

```dotenv
APP_ENV=production
APP_DEBUG=false
DATABASE_URL=postgresql+asyncpg://skrezo:<PG_PASS>@127.0.0.1:6432/skrezo        # через PgBouncer
DATABASE_DIRECT_URL=postgresql+asyncpg://skrezo:<PG_PASS>@127.0.0.1:5432/skrezo # LISTEN + миграции
REDIS_URL=redis://:<REDIS_PASS>@127.0.0.1:6379/0
ROOT_PATH=                                   # свой домен — пусто; поддиректория /skrz — см. README
PUBLIC_API_URL=https://api.skrezo.com/api/v1  # колбэки FreedomPay
CABINET_URL=https://office.skrezo.com         # возврат из оплаты; только https, без www
CORS_ORIGINS=https://office.skrezo.com
STORAGE_ROOT=storage                          # /opt/skrezo/app/storage
APNS_KEY=secrets/AuthKey_XXXXXXXX.p8          # если PUSH_ENABLED=true
FCM_SERVICE_ACCOUNT_JSON=secrets/fcm-service-account.json
```

`.env` читает само приложение из рабочего каталога юнита, поэтому комментарии
после значений допустимы. Проверка, что настройки собираются (секреты не печатает):

```bash
sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && .venv/bin/python -c "
from app.core.config import settings
print(settings.app_env, settings.database_url.rsplit(\"@\", 1)[-1], settings.listen_dsn.rsplit(\"@\", 1)[-1])"'
```

## 7. Миграции (пустая база) или перенос данных (раздел 8)

С нуля — миграции создадут таблицы и справочники, затем первый админ Skrezo:

```bash
sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && .venv/bin/alembic upgrade head'
sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && .venv/bin/python -m app.scripts.create_platform_user --phone 0700000001 --password "СИЛЬНЫЙ_ПАРОЛЬ"'
```

## 8. Перенос данных со старого сервера (Docker)

На **старом** сервере — остановить запись и снять дамп:

```bash
cd /путь/к/проекту
docker compose stop api worker                                    # с этого момента — простой
docker compose exec -T postgres pg_dump -U skrezo -Fc skrezo > /tmp/skrezo.dump
scp /tmp/skrezo.dump root@НОВЫЙ:/tmp/skrezo.dump
rsync -a ./storage/ root@НОВЫЙ:/opt/skrezo/app/storage/
```

На **новом**:

```bash
sudo -u postgres pg_restore --dbname=skrezo --no-owner --role=skrezo --jobs=4 /tmp/skrezo.dump
# «extension already exists» / «must be owner of extension» — ожидаемо, данные восстановлены.
chown -R skrezo:skrezo /opt/skrezo/app/storage
sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && .venv/bin/alembic upgrade head'   # догоняет новые миграции
rm /tmp/skrezo.dump
```

## 9. systemd: API и воркеры

```bash
cp /opt/skrezo/app/deploy/systemd/* /etc/systemd/system/
systemctl daemon-reload
systemctl enable skrezo.target skrezo-api@8001 skrezo-api@8002 skrezo-worker@1 skrezo-worker@2 skrezo-backup.timer
systemctl start skrezo.target skrezo-backup.timer

systemctl status 'skrezo-*' --no-pager
curl -s http://127.0.0.1:8001/health && curl -s http://127.0.0.1:8002/health
journalctl -u skrezo-worker@1 -n 30 --no-pager       # event_listener_connected, cron'ы
journalctl -u skrezo-api@8001 -n 30 --no-pager       # realtime_listener_connected
```

Автозапуск: `skrezo.target` включён в `multi-user.target`, инстансы — в
`skrezo.target.wants`; `Restart=always` поднимает упавший процесс через 3–5 с.

## 10. nginx и TLS

```bash
cp /opt/skrezo/app/deploy/nginx/skrezo.conf /etc/nginx/sites-available/skrezo
sed -i 's/api\.skrezo\.com/api.ВАШ-ДОМЕН/g' /etc/nginx/sites-available/skrezo
ln -s /etc/nginx/sites-available/skrezo /etc/nginx/sites-enabled/skrezo
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# DNS уже должен указывать на сервер (проверка HTTP-01). certbot допишет TLS и редирект 80→443.
certbot --nginx -d api.ВАШ-ДОМЕН --redirect --agree-tos -m admin@ВАШ-ДОМЕН
systemctl list-timers certbot.timer     # автопродление сертификата
```

## 11. Проверка

```bash
curl -s https://api.ВАШ-ДОМЕН/health                              # {"status":"ok"}
curl -sI https://api.ВАШ-ДОМЕН/docs | head -1                     # HTTP/2 200
curl -s 'https://api.ВАШ-ДОМЕН/socket.io/?EIO=4&transport=polling' # 0{"sid":...} — сокет за nginx жив
psql "postgresql://skrezo:$PG_PASS@127.0.0.1:6432/pgbouncer" -c 'SHOW POOLS;'
```

## 12. Внешние системы после переезда

- **Meta / WhatsApp Cloud API** — новый Callback URL вебхука
  `https://api.ВАШ-ДОМЕН/api/v1/whatsapp/webhook` и тот же `WA_CLOUD_VERIFY_TOKEN`;
  после сохранения — подписки на `messages` заново.
- **FreedomPay** — `result_url` формируется из `PUBLIC_API_URL`
  (`…/api/v1/payments/freedompay/result`); если в кабинете мерчанта адреса
  зафиксированы, обновить их там.
- **Кабинет и мобильные приложения** — новый базовый адрес API.
- **Шлюзы OTP/SMS**, если у них белый список IP, — добавить IP сервера.

## Эксплуатация

**Деплой без простоя** (код → зависимости → миграции → API по одному → воркеры):

```bash
sudo /opt/skrezo/app/deploy/bin/deploy.sh            # ветка main
sudo /opt/skrezo/app/deploy/bin/deploy.sh v1.4.0     # тег или коммит — так же делается откат
```

**Логи**: `journalctl -u skrezo-api@8001 -f`, `journalctl -u 'skrezo-worker@*' -f`,
nginx — `/var/log/nginx/`, PgBouncer — `/var/log/postgresql/pgbouncer.log`,
Postgres — `/var/log/postgresql/postgresql-16-main.log` (медленные запросы > 500 мс).

**Перезапуск всего**: `systemctl restart skrezo.target`. Один инстанс:
`systemctl restart skrezo-api@8001`.

**Бэкапы**: таймер `skrezo-backup.timer` в 01:30 UTC кладёт дамп и зеркало
`storage/` в `/var/backups/skrezo` (14 дней). Вручную —
`/opt/skrezo/app/deploy/bin/backup.sh`. Восстановление:
`sudo -u postgres pg_restore --clean --if-exists --dbname=skrezo --no-owner --role=skrezo ФАЙЛ.dump`.
Копия лежит на том же диске — каталог стоит зеркалить на другой хост (rclone/rsync).

**Масштабирование**: ещё один API — `systemctl enable --now skrezo-api@8003` и
строка `server 127.0.0.1:8003;` в upstream nginx; ещё воркер —
`systemctl enable --now skrezo-worker@3`. Размер пула БД на процесс — `DB_POOL_SIZE` /
`DB_MAX_OVERFLOW`, потолок PgBouncer — `default_pool_size`.

**Откат миграции** (только если релиз её требует):
`sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && .venv/bin/alembic downgrade -1'` —
затем `deploy.sh <предыдущий тег>`.
