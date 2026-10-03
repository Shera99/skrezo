#!/usr/bin/env bash
# Деплой Skrezo без простоя: код → юниты systemd → зависимости → миграции → API по одному → воркеры.
#   sudo /opt/skrezo/app/deploy/bin/deploy.sh            # ветка main
#   sudo /opt/skrezo/app/deploy/bin/deploy.sh v1.4.0     # тег или коммит
# Миграции идут по DATABASE_DIRECT_URL (мимо PgBouncer) и ровно один раз — не в
# ExecStartPre юнитов: два инстанса API запускали бы alembic наперегонки.
set -euo pipefail

APP_DIR=/opt/skrezo/app
APP_USER=skrezo
REF=${1:-main}
API_PORTS=(8001 8002)
WORKERS=(1 2)

as_app() { sudo -u "$APP_USER" -H "$@"; }

cd "$APP_DIR"
echo "→ код: $REF"
as_app git fetch --tags --prune origin
if as_app git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
    as_app git checkout -q -B "$REF" "origin/$REF"   # ветка
else
    as_app git checkout -q "$REF"                    # тег или коммит
fi

echo "→ юниты systemd"
# Источник истины — deploy/systemd в репозитории: изменённые файлы копируются сами.
# Свои правки на сервере — только через drop-in (`systemctl edit skrezo-api@.service`),
# иначе следующий деплой их перезапишет.
units_changed=0
for unit in deploy/systemd/*; do
    target="/etc/systemd/system/$(basename "$unit")"
    if ! cmp -s "$unit" "$target"; then
        install -m 644 "$unit" "$target"
        echo "  обновлён $(basename "$unit")"
        units_changed=1
    fi
done
if [ "$units_changed" -eq 1 ]; then
    systemctl daemon-reload
    systemctl try-restart 'skrezo-*.timer' || true   # таймер — не повод срывать деплой
fi

echo "→ зависимости (uv sync --frozen --no-dev --compile-bytecode)"
# Байткод — сразу при установке: иначе первый старт после деплоя компилирует
# библиотеки на ходу, и окно перезапуска API растягивается.
as_app uv sync --frozen --no-dev --compile-bytecode

echo "→ миграции"
as_app .venv/bin/alembic upgrade head

echo "→ API по одному"
for port in "${API_PORTS[@]}"; do
    systemctl restart "skrezo-api@$port"
    for i in $(seq 1 30); do
        if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
            echo "  skrezo-api@$port: ok"
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "  skrezo-api@$port не поднялся — смотрите journalctl -u skrezo-api@$port" >&2
            journalctl -u "skrezo-api@$port" -n 40 --no-pager >&2
            exit 1
        fi
        sleep 1
    done
done

echo "→ воркеры"
for n in "${WORKERS[@]}"; do
    systemctl restart "skrezo-worker@$n"
done
sleep 3
for n in "${WORKERS[@]}"; do
    systemctl --quiet is-active "skrezo-worker@$n" && echo "  skrezo-worker@$n: ok" \
        || { echo "  skrezo-worker@$n упал — journalctl -u skrezo-worker@$n" >&2; exit 1; }
done

echo "готово: $(as_app git rev-parse --short HEAD)"
