#!/usr/bin/env bash
# Бэкап PostgreSQL WeBook: pg_dump из контейнера webook-db → gzip → ротация.
#
# Использование (на сервере):
#   /opt/webook/scripts/pg_backup.sh
# Переменные окружения (необязательно):
#   BACKUP_DIR (по умолчанию /opt/webook/backups), KEEP (14),
#   DB_CONTAINER (webook-db), DB_USER (webook), DB_NAME (webook)
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/webook/backups}"
KEEP="${KEEP:-14}"
DB_CONTAINER="${DB_CONTAINER:-webook-db}"
DB_USER="${DB_USER:-webook}"
DB_NAME="${DB_NAME:-webook}"

mkdir -p "$BACKUP_DIR"
TS="$(date +%Y-%m-%d_%H%M)"
OUT="$BACKUP_DIR/webook_${TS}.sql.gz"

docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$OUT"
echo "[pg_backup] wrote $OUT ($(du -h "$OUT" | cut -f1))"

# Ротация: оставить последние $KEEP дампов
ls -1t "$BACKUP_DIR"/webook_*.sql.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
