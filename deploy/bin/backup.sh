#!/usr/bin/env bash
# Ночной бэкап Skrezo: дамп Postgres (custom-формат, восстанавливается pg_restore)
# и зеркало storage/ (снимки, документы, голосовые). Хранит дампы KEEP_DAYS дней.
# Запускается от root таймером skrezo-backup.timer; вручную: sudo deploy/bin/backup.sh
#
# Копия на том же диске спасает от ошибки оператора, но не от потери сервера —
# каталог /var/backups/skrezo стоит зеркалить наружу (rclone/rsync на другой хост).
set -euo pipefail

BACKUP_DIR=/var/backups/skrezo
APP_DIR=/opt/skrezo/app
KEEP_DAYS=14
STAMP=$(date -u +%Y%m%d-%H%M)

mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/storage"
chmod 700 "$BACKUP_DIR"

# Дамп — прямо в Postgres (peer-auth пользователя postgres), мимо PgBouncer.
runuser -u postgres -- pg_dump --format=custom --dbname=skrezo \
    > "$BACKUP_DIR/db/skrezo-$STAMP.dump"

rsync -a --delete "$APP_DIR/storage/" "$BACKUP_DIR/storage/"

find "$BACKUP_DIR/db" -name 'skrezo-*.dump' -mtime +"$KEEP_DAYS" -delete
echo "backup ok: $BACKUP_DIR/db/skrezo-$STAMP.dump"
