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
wa-service :5001 (Node + Chrome, снаружи закрыт ufw) — WhatsApp-шлюз для OTP    skrezo-wa
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
| `deploy/systemd/skrezo-wa.service` | `/etc/systemd/system/` — WhatsApp-шлюз wa-service (раздел 13) |
| `deploy/wa/wa.env` | `/etc/skrezo-wa/wa.env` — настройки шлюза |
| `deploy/wa/pair.html` | открывается на вашем компьютере — привязка номера по QR |
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
```

SSH — только по ключу, root не входит. Публичный ключ уже должен лежать в
`~/.ssh/authorized_keys` вашего пользователя с sudo (ниже — `skrezouser`; подставьте
своё имя из `whoami`, иначе закроете себе вход). Текущую сессию не закрывайте, пока
новое подключение не проверено:

```bash
cat > /etc/ssh/sshd_config.d/10-skrezo.conf <<'CONF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
MaxAuthTries 4
LoginGraceTime 30
AllowUsers skrezouser
X11Forwarding no
CONF
sshd -t && systemctl restart ssh      # sshd -t — проверка синтаксиса до перезапуска
```

Файл `10-…` читается раньше `50-cloud-init.conf`, где облачный образ оставляет
`PasswordAuthentication yes`, — побеждает первое значение, то есть наше. Пароль
пользователю **не блокировать** (`passwd -l`): он нужен `sudo`; по SSH пароль уже не
принимается. Проверка из второго окна: `ssh skrezouser@СЕРВЕР` входит без пароля,
`ssh root@СЕРВЕР` — отказ.

fail2ban — бан на сутки после 5 неудач за 10 минут; `backend = systemd` обязателен:
без него на 24.04 jail не находит журнал sshd и не стартует:

```bash
cat > /etc/fail2ban/jail.local <<'CONF'
[DEFAULT]
bantime = 24h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
backend = systemd
CONF
systemctl enable --now fail2ban && systemctl restart fail2ban
fail2ban-client status sshd
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
sudo -u skrezo -H mkdir -p /opt/skrezo/app/storage      # файлы пациентов; в git его нет
```

Git в этом каталоге — только от `skrezo` (`sudo -u skrezo -H git -C /opt/skrezo/app pull`),
так делает и `deploy.sh`. От root git откажет («dubious ownership»), а если добавить
исключение, root-овские файлы в `.git` сломают следующий pull от `skrezo`.

Чтобы не набирать `sudo -u skrezo …` каждый раз, заведите ярлык в `~/.bashrc` своего
пользователя (выполнить один раз от себя, не от root):

```bash
echo "alias skr='sudo -iu skrezo bash -c \"cd /opt/skrezo/app && source .venv/bin/activate && exec bash\"'" >> ~/.bashrc && source ~/.bashrc
```

Теперь `skr` открывает оболочку от `skrezo` прямо в `/opt/skrezo/app` с активным
`.venv`: `git pull`, `alembic upgrade head`, `python -m app.scripts…` работают как есть,
выход — `exit`. Повседневный деплой ярлыка не требует: `sudo …/deploy/bin/deploy.sh`
из-под своего пользователя делает всё сам.

uv ставится в `/usr/local/bin`; Python 3.14 (в Ubuntu 24.04 его нет) uv скачает
сам в `/opt/skrezo/.local/share/uv/python` — версия зафиксирована в
`.python-version`, зависимости — в `uv.lock`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
uv --version
sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && uv python install 3.14 --compile-bytecode && \
    uv sync --frozen --no-dev --compile-bytecode'
sudo -u skrezo /opt/skrezo/app/.venv/bin/python --version      # Python 3.14.x

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

Серверы друг друга обычно не видят, а root по SSH на новый уже не входит, поэтому
файлы идут через ваш компьютер под вашим пользователем, а на место их переносит root.

С вашего компьютера (`.env` и `secrets/` — со старого прода или из `.env.example`):

```bash
scp .env skrezouser@НОВЫЙ:~/skrezo.env
scp -r secrets skrezouser@НОВЫЙ:~/skrezo-secrets
```

На сервере от root:

```bash
mv /home/skrezouser/skrezo.env /opt/skrezo/app/.env
mv /home/skrezouser/skrezo-secrets/* /opt/skrezo/app/secrets/ && rmdir /home/skrezouser/skrezo-secrets
chown -R skrezo:skrezo /opt/skrezo/app/.env /opt/skrezo/app/secrets
chmod 600 /opt/skrezo/app/.env /opt/skrezo/app/secrets/* && chmod 700 /opt/skrezo/app/secrets
sudo -u skrezo cat /opt/skrezo/app/.env /opt/skrezo/app/secrets/* > /dev/null && echo "ключи читаются"
```

`chown` — строго после `mv`: перенесённый файл сохраняет владельца (`skrezouser`), и
воркер получит «Permission denied». Пуши при этом не падают громко — в логе
`fcm_channel_init_failed` и `push_transport_built android=log ios=log`, то есть
уведомления пишутся в журнал вместо отправки.

Что меняется в `.env` под этот сервер (остальное — как на старом проде):

```dotenv
APP_ENV=production
APP_DEBUG=false
DATABASE_URL=postgresql+asyncpg://skrezo:<PG_PASS>@127.0.0.1:6432/skrezo        # через PgBouncer
DATABASE_DIRECT_URL=postgresql+asyncpg://skrezo:<PG_PASS>@127.0.0.1:5432/skrezo # LISTEN + миграции
REDIS_URL=redis://:<REDIS_PASS>@127.0.0.1:6379/0
ROOT_PATH=                                   # свой домен — пусто; поддиректория /skrz — см. DEVELOPMENT.md
PUBLIC_API_URL=https://api.skrezo.com/api/v1  # колбэки FreedomPay
CABINET_URL=https://office.skrezo.com         # возврат из оплаты; только https, без www
CORS_ORIGINS=https://office.skrezo.com
STORAGE_ROOT=storage                          # /opt/skrezo/app/storage
UPLOAD_CT_MAX_BYTES=1073741824                # КТ до 1 ГиБ; = client_max_body_size в nginx
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
```

Переслать на новый сервер под вашим пользователем: со старого сервера напрямую
(его публичный ключ нужно добавить в `~/.ssh/authorized_keys` пользователя на новом)
либо в два приёма через свой компьютер:

```bash
scp /tmp/skrezo.dump skrezouser@НОВЫЙ:~/skrezo.dump
rsync -a ./storage/ skrezouser@НОВЫЙ:~/skrezo-storage/
```

На **новом** от root:

```bash
install -o postgres -m 600 /home/skrezouser/skrezo.dump /tmp/skrezo.dump && rm /home/skrezouser/skrezo.dump
sudo -u postgres pg_restore --dbname=skrezo --no-owner --role=skrezo --jobs=4 /tmp/skrezo.dump
# «extension already exists» / «must be owner of extension» — ожидаемо, данные восстановлены.
rsync -a /home/skrezouser/skrezo-storage/ /opt/skrezo/app/storage/ && rm -r /home/skrezouser/skrezo-storage
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
journalctl -u skrezo-worker@1 -n 30 --no-pager       # event_listener_connected, cron'ы;
                                                     # при PUSH_ENABLED=true — push_transport_built android=fcm ios=apns
journalctl -u skrezo-api@8001 -n 30 --no-pager       # realtime_listener_connected
```

Автозапуск: `skrezo.target` включён в `multi-user.target`, инстансы — в
`skrezo.target.wants`; `Restart=always` поднимает упавший процесс через 3–5 с.

## 10. nginx и TLS

Домен API и почту для писем Let's Encrypt задайте один раз в сессии — команды ниже
берут их из переменных:

```bash
DOMAIN=api.skrezo.com                  # ваш домен API
EMAIL=admin@skrezo.com                 # сюда Let's Encrypt пишет об истечении сертификата
getent ahostsv4 "$DOMAIN" | head -1    # должен быть IP этого сервера
```

Другой адрес — DNS ещё не обновился (подождать) либо домен за прокси Cloudflare
(см. ниже).

```bash
cp /opt/skrezo/app/deploy/nginx/skrezo.conf /etc/nginx/sites-available/skrezo
sed -i "s/api\.skrezo\.com/$DOMAIN/g" /etc/nginx/sites-available/skrezo
ln -sf /etc/nginx/sites-available/skrezo /etc/nginx/sites-enabled/skrezo
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
curl -s "http://$DOMAIN/health"        # {"status":"ok"} — nginx уже проксирует в API

# Проверка HTTP-01 идёт на этот сервер; certbot допишет TLS и редирект 80→443.
certbot --nginx -d "$DOMAIN" --redirect --agree-tos --no-eff-email -m "$EMAIL"
systemctl list-timers certbot.timer    # автопродление; nginx перечитывается сам
```

### Загрузка КТ

Исследование КТ (DICOM или ZIP) весит сотни мегабайт, поэтому у ручки файлов
пациента в `skrezo.conf` свой `location` с `client_max_body_size 1100m` — он
должен быть не меньше `UPLOAD_CT_MAX_BYTES` (+ запас на multipart), и не больше
трёх одновременных загрузок с одного адреса (`limit_conn skrezo_uploads`; зона
объявлена в начале того же файла). Запрос без валидного токена API отвергает до
приёма тела, поэтому гигабайт во временный каталог кладёт только вошедший
пользователь. Загрузка
проходит через временный каталог uvicorn (`PrivateTmp` юнита) и только затем
ложится в `storage/ct/`, так что на диске нужен запас примерно в два размера
файла, а ночной бэкап `storage/` растёт вместе с КТ.

⚠️ Если домен за прокси Cloudflare (оранжевое облако), тело запроса режется на его
стороне: на бесплатном тарифе — 100 МБ, и КТ больше этого получит 413 ещё до
nginx. Для загрузки КТ домен API должен быть в режиме **DNS only** либо на тарифе
с бо́льшим лимитом.

### Если домен за Cloudflare

Проще всего — запись в режиме **DNS only** (серое облако): всё работает как описано.
Если нужен прокси (оранжевое облако), сертификат выпускайте при сером облаке, затем
включите оранжевое с режимом SSL **Full (strict)** и научите nginx брать реальный IP
клиента из `CF-Connecting-IP`. Без этого все запросы приходят с адресов Cloudflare:
лимиты по IP (OTP, форма заявки) превращаются в один общий счётчик, а `ip_hash`
раскидывает запросы одного клиента Socket.IO по разным процессам.

```bash
{ curl -fsS https://www.cloudflare.com/ips-v4; echo; curl -fsS https://www.cloudflare.com/ips-v6; echo; } \
    | grep -E '^[0-9a-f:.]+/[0-9]+$' | sed 's/^/set_real_ip_from /; s/$/;/' \
    > /etc/nginx/conf.d/cloudflare-realip.conf
echo 'real_ip_header CF-Connecting-IP;' >> /etc/nginx/conf.d/cloudflare-realip.conf
nginx -t && systemctl reload nginx
```

Список адресов Cloudflare меняется редко — команду стоит повторять раз в несколько месяцев.

## 11. Проверка

```bash
curl -s "https://$DOMAIN/health"                                    # {"status":"ok"}
curl -sI "https://$DOMAIN/docs" | head -1                           # … 200
curl -s "https://$DOMAIN/socket.io/?EIO=4&transport=polling"; echo  # 0{"sid":…} — сокет за nginx жив
curl -si --http1.1 --max-time 3 -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    "https://$DOMAIN/socket.io/?EIO=4&transport=websocket" | head -1   # HTTP/1.1 101 Switching Protocols
psql "postgresql://skrezo:$PG_PASS@127.0.0.1:6432/pgbouncer" -c 'SHOW POOLS;'
```

Затем сверьте в `.env` адреса под новый домен — `PUBLIC_API_URL`, `CABINET_URL`,
`CORS_ORIGINS`; если что-то правили, `systemctl restart skrezo.target`.

## 12. Внешние системы после переезда

- **Meta / WhatsApp Cloud API** — новый Callback URL вебхука
  `https://api.ВАШ-ДОМЕН/api/v1/whatsapp/webhook` и тот же `WA_CLOUD_VERIFY_TOKEN`;
  после сохранения — подписки на `messages` заново.
- **FreedomPay** — `result_url` формируется из `PUBLIC_API_URL`
  (`…/api/v1/payments/freedompay/result`); если в кабинете мерчанта адреса
  зафиксированы, обновить их там.
- **Кабинет и мобильные приложения** — новый базовый адрес API.
- **Шлюзы OTP/SMS**, если у них белый список IP, — добавить IP сервера.

### Подключение номеров самими клиниками (Embedded Signup)

Клиника подключает свой номер WhatsApp из кабинета через окно Meta; номер
остаётся и в приложении WhatsApp Business. Для этого один раз:

1. **Приложение Meta Skrezo** — бизнес подтверждён, приложение проверено Meta на
   права `whatsapp_business_management` и `whatsapp_business_messaging`
   (статус Tech Provider). Без этого окно работает только для тестировщиков
   приложения.
2. **Facebook Login for Business → конфигурация** с вариантом входа
   «WhatsApp Embedded Signup»; её ID — в `WA_EMBEDDED_SIGNUP_CONFIG_ID`, ID
   приложения — в `WA_CLOUD_APP_ID` (`WA_CLOUD_APP_SECRET` уже задан для вебхука).
3. **Домен кабинета** (`CABINET_URL`, только https) — в разрешённые домены JS SDK
   и Facebook Login приложения.
4. **Вебхуки → WhatsApp Business Account**: подписать поля `messages`,
   `smb_message_echoes`, `history`, `smb_app_state_sync`, `account_update`.
   Без `history` и `smb_app_state_sync` окно не предложит номер из приложения.
5. **Ключ шифрования токенов клиник** — в `.env` как `DATA_ENCRYPTION_KEY`:

```bash
sudo -u skrezo /opt/skrezo/app/.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Ключ хранить отдельно от бэкапов базы: потеряете его — все клиники, подключённые
сами, должны будут пройти окно Meta заново. Ротация — новый ключ первым через
запятую (`DATA_ENCRYPTION_KEY=новый,старый`), старый убрать после перешифровки.
После правки `.env` — перезапуск API и воркеров. Кнопка подключения появляется
в кабинете, когда заданы все четыре значения (`GET /clinic/whatsapp` →
`signup.available: true`).

## 13. WhatsApp-шлюз для OTP (wa-service)

Коды входа и уведомления сотрудникам уходят по цепочкам `OTP_CHANNELS` и
`NOTIFY_CHANNELS`. Канал `whatsapp` шлёт `POST {accountId, to, message}` на
`WHATSAPP_API_URL` и ждёт в ответе `success` — это API wa-service из репозитория
[identity-service](https://github.com/Shera99/identity-service), папка `wa-service/`.
На этом сервере поднимается только он: auth_service, панель и Docker не нужны, код
Skrezo не меняется. wa-service держит в headless Chrome сессию WhatsApp Web,
привязанную к обычному номеру как «связанное устройство».

⚠️ До установки:

- **Способ неофициальный.** WhatsApp может заблокировать номер, особенно новый,
  который пишет незнакомым. Нужна отдельная SIM-карта — не личный номер и не номер
  клиники. `sms` в цепочке остаётся последним: при блокировке или обрыве сессии коды
  уйдут по SMS.
- **Телефон с этой SIM** должен выходить в сеть хотя бы раз в 14 дней, иначе WhatsApp
  отвяжет связанные устройства, и номер придётся привязывать заново.
- **У wa-service нет авторизации**, и слушает он все интерфейсы. Кто достучится до
  порта 5001, отправит сообщение с вашего номера и прочитает журнал отправленных
  вместе с кодами. Снаружи порт закрывает только ufw из раздела 1: в ufw его не
  открывать, в nginx не проксировать.
- **Обновления WhatsApp Web** иногда ломают whatsapp-web.js: аккаунт уходит в
  `FAILED`, в журнале `Ready timeout`. Лечится обновлением библиотеки в
  identity-service; до тех пор коды идут по SMS.
- Процессор — **x86_64**: Chrome, который скачивает puppeteer, под arm64-Linux не
  выпускается.

### 13.1 Node.js 22

В Ubuntu 24.04 Node 18, его поддержка закончилась; LTS берём из репозитория NodeSource:

```bash
uname -m                                # x86_64
install -d -m 755 /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list
apt update && apt -y install nodejs
node --version                          # v22.x
```

### 13.2 Пользователь и код

Отдельный пользователь `skrezo-wa`: у Chrome большая поверхность атаки, а к коду,
`.env` и ключам Skrezo доступа у него нет. Из identity-service забирается только
папка `wa-service`. Deploy-ключ GitHub привязывается к одному репозиторию, поэтому
у этого пользователя свой:

```bash
useradd --system --create-home --home-dir /opt/skrezo-wa --shell /usr/sbin/nologin skrezo-wa
chmod 750 /opt/skrezo-wa
sudo -u skrezo-wa -H bash -c 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
    ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C skrezo-wa && \
    ssh-keyscan github.com >> ~/.ssh/known_hosts'
cat /opt/skrezo-wa/.ssh/id_ed25519.pub   # → identity-service → Settings → Deploy keys, только чтение

sudo -u skrezo-wa -H git clone --filter=blob:none --sparse \
    git@github.com:Shera99/identity-service.git /opt/skrezo-wa/identity-service
sudo -u skrezo-wa -H git -C /opt/skrezo-wa/identity-service sparse-checkout set wa-service
```

### 13.3 Зависимости и Chrome

`npm ci` вместе с пакетами скачивает Chrome той версии, под которую собран
puppeteer, в `/opt/skrezo-wa/puppeteer` — тот же путь стоит в юните как
`PUPPETEER_CACHE_DIR`. Системные библиотеки для Chrome ставит `--install-deps`
через apt, поэтому вторая команда — от root:

```bash
sudo -u skrezo-wa -H bash -c 'cd /opt/skrezo-wa/identity-service/wa-service && \
    PUPPETEER_CACHE_DIR=/opt/skrezo-wa/puppeteer npm ci --omit=dev && npx prisma generate'
(cd /opt/skrezo-wa/identity-service/wa-service && \
    PUPPETEER_CACHE_DIR=/opt/skrezo-wa/puppeteer npx puppeteer browsers install chrome --install-deps)
```

### 13.4 База

Своя база `skrezo_wa` в том же Postgres: таблицы шлюза не смешиваются со схемой
Skrezo и не попадают в autogenerate Alembic. Подключение — напрямую на 5432,
PgBouncer настроен только на базу `skrezo`. Схему `wa` создаём сами (Prisma её не
создаёт), таблицы — `prisma db push` по схеме из кода wa-service. Пароль нужен
только в `wa.env`, записывать его не обязательно:

```bash
WA_PG_PASS=$(openssl rand -hex 24)
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE skrezo_wa LOGIN PASSWORD '$WA_PG_PASS';
CREATE DATABASE skrezo_wa OWNER skrezo_wa;
\c skrezo_wa
CREATE SCHEMA wa AUTHORIZATION skrezo_wa;
SQL

install -d -m 750 -o root -g skrezo-wa /etc/skrezo-wa
install -m 640 -o root -g skrezo-wa /opt/skrezo/app/deploy/wa/wa.env /etc/skrezo-wa/wa.env
sed -i "s/ЗАМЕНИТЬ_ПАРОЛЬ/$WA_PG_PASS/g" /etc/skrezo-wa/wa.env

sudo -u skrezo-wa -H bash -c 'set -a && . /etc/skrezo-wa/wa.env && \
    cd /opt/skrezo-wa/identity-service/wa-service && npx prisma db push --skip-generate'
```

В конце — `Your database is now in sync with your Prisma schema`.

### 13.5 Запуск

Юнит лежит в `deploy/systemd/` вместе с остальными (раздел 9 и `deploy.sh` копируют
его сами), но в `skrezo.target` не входит: деплой Skrezo сессию WhatsApp не трогает.

```bash
install -m 644 /opt/skrezo/app/deploy/systemd/skrezo-wa.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now skrezo-wa
curl -s http://127.0.0.1:5001/health; echo         # "status":"ok", "activeClients":0
```

С вашего компьютера — порт снаружи закрыт, команда должна закончиться таймаутом:

```bash
curl --max-time 5 http://IP_СЕРВЕРА:5001/health
```

### 13.6 Привязка номера

QR-код показывает страница [`deploy/wa/pair.html`](../deploy/wa/pair.html): она
открывается у вас в браузере и ходит в wa-service через SSH-туннель. На своём
компьютере, в отдельном окне терминала — держать открытым, пока идёт привязка:

```bash
ssh -N -L 5001:127.0.0.1:5001 skrezouser@СЕРВЕР
```

Затем откройте `deploy/wa/pair.html` из репозитория в браузере:

1. «Создать аккаунт» (название любое, например `Skrezo OTP`) → «Подключить».
2. Через 10–30 с появится QR. На телефоне с SIM шлюза: WhatsApp → Настройки →
   Связанные устройства → Привязка устройства — отсканировать.
3. Статус `CONNECTED` → «Скопировать ID»: это `WHATSAPP_ACCOUNT_ID`.
4. «Пробное сообщение» на свой номер — должно прийти в WhatsApp.

Сессия хранится в `/var/lib/skrezo-wa/.wwebjs_auth`: после перезапуска сервиса или
сервера QR не нужен.

### 13.7 Включить в Skrezo

В `/opt/skrezo/app/.env` (комментарии — отдельной строкой: `KEY=  # текст` станет
значением):

```dotenv
WHATSAPP_API_URL=http://127.0.0.1:5001/api/messages/send
WHATSAPP_ACCOUNT_ID=ID_ИЗ_PAIR_HTML
# порядок = приоритет; sms — запасной, Telegram позже: telegram,whatsapp,sms
OTP_CHANNELS=whatsapp,sms
NOTIFY_CHANNELS=whatsapp,sms
# первое сообщение новому номеру шлюз отправляет дольше обычного
OTP_DELIVERY_TIMEOUT_SECONDS=10
NOTIFY_DELIVERY_TIMEOUT_SECONDS=10
```

```bash
systemctl restart skrezo.target
```

Проверка — вход в приложение со своего номера: код приходит в WhatsApp, а в журнале
API строка `otp_code_sent` с `channel=whatsapp`:

```bash
journalctl -u 'skrezo-api@*' --since '10 min ago' --no-pager | grep -E 'otp_code_sent|otp_channel_failed'
```

`otp_channel_failed` с `channel=whatsapp` — шлюз отказал, причина в `error`, код ушёл
следующим каналом. `HTTP 400` — аккаунт не подключён (см. «Шлюз WhatsApp» ниже).

## Эксплуатация

Всё ниже выполняется из-под своего пользователя с `sudo`; `sudo -i` и переходы в каталог
проекта нужны были только при установке.

Полная шпаргалка — [SERVER_OPS.html](SERVER_OPS.html): все команды по сервисам, у каждой
описание и ожидаемый результат, поиск и подстановка домена. Новые команды дописываются в
`scripts/server_ops/content.py`, страница пересобирается `make server-ops`. Ниже — самое основное.

**Деплой без простоя** (код → юниты systemd → зависимости → миграции → API по одному →
воркеры). Изменённые файлы `deploy/systemd/*` скрипт копирует в `/etc/systemd/system` сам;
свои правки юнитов на сервере — только через `systemctl edit …` (drop-in), иначе деплой
их перезапишет:

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
каталог бэкапов закрыт для `postgres`, поэтому дамп сначала копируется ему:
`sudo install -o postgres -m 600 /var/backups/skrezo/db/ФАЙЛ.dump /tmp/restore.dump`, затем
`sudo -u postgres pg_restore --clean --if-exists --dbname=skrezo --no-owner --role=skrezo /tmp/restore.dump`
при остановленном `skrezo.target`.
Копия лежит на том же диске — каталог стоит зеркалить на другой хост (rclone/rsync).

**Масштабирование**: ещё один API — `systemctl enable --now skrezo-api@8003` и
строка `server 127.0.0.1:8003;` в upstream nginx; ещё воркер —
`systemctl enable --now skrezo-worker@3`. Размер пула БД на процесс — `DB_POOL_SIZE` /
`DB_MAX_OVERFLOW`, потолок PgBouncer — `default_pool_size`.

**Откат миграции** (только если релиз её требует):
`sudo -u skrezo -H bash -c 'cd /opt/skrezo/app && .venv/bin/alembic downgrade -1'` —
затем `deploy.sh <предыдущий тег>`.

### Шлюз WhatsApp

- **Логи**: `journalctl -u skrezo-wa -f`. Раз в минуту — блок `Resource Monitor`, это норма.
- **Состояние**: `curl -s 127.0.0.1:5001/health` — в `clients` аккаунт со статусом
  `CONNECTED`.
- **После перезапуска** сервис сам поднимает аккаунты, которые были `CONNECTED`. Если
  аккаунт в `DISCONNECTED` или `FAILED` — «Подключить» в `pair.html` или
  `curl -X POST 127.0.0.1:5001/api/accounts/ID/connect`: сохранённая сессия
  поднимется без QR. QR понадобится, только если WhatsApp отвязал устройство.
- **Перезапуск**: `systemctl restart skrezo-wa` — только сам шлюз; `deploy.sh` и
  `restart skrezo.target` его не трогают. Пока шлюз поднимается (до минуты), коды
  уходят по SMS.
- **Обновление wa-service** (после правок в identity-service):

  ```bash
  sudo -u skrezo-wa -H git -C /opt/skrezo-wa/identity-service pull
  sudo -u skrezo-wa -H bash -c 'cd /opt/skrezo-wa/identity-service/wa-service && \
      PUPPETEER_CACHE_DIR=/opt/skrezo-wa/puppeteer npm ci --omit=dev && npx prisma generate'
  systemctl restart skrezo-wa
  ```

  Если в обновлении менялась `prisma/schema.prisma` — перед перезапуском `prisma db push`
  из раздела 13.4; если сменилась версия puppeteer — ещё раз `--install-deps` из 13.3.
- **Журнал отправленных** `wa.messages` хранит тексты вместе с кодами. Коды живут
  минуты, но таблица растёт — чистить раз в месяц:
  `sudo -u postgres psql -d skrezo_wa -c "DELETE FROM wa.messages WHERE \"sentAt\" < now() - interval '30 days'"`.
- **Бэкап** базы `skrezo_wa` и сессии не нужен: при потере — заново пройти 13.4 и 13.6.
