#!/bin/sh
# Encrypted, content-addressed database backup. Never accepts credentials as arguments.
set -eu
umask 077

BACKUP_DIR=${KPV3_BACKUP_DIR:-/var/backups/kalshi-v3}
AGE_RECIPIENT_FILE=${KPV3_AGE_RECIPIENT_FILE:-/run/secrets/backup_age_recipient}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

test -s "$AGE_RECIPIENT_FILE"
command -v age >/dev/null
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

docker compose exec -T postgres pg_dump -U kalshi -d kalshi --format=custom \
  --no-owner --no-privileges > "$TMP/postgres.dump"
test -s "$TMP/postgres.dump"
OUTPUT="postgres-$STAMP.dump.age"
age -R "$AGE_RECIPIENT_FILE" -o "$BACKUP_DIR/$OUTPUT" "$TMP/postgres.dump"
(cd "$BACKUP_DIR" && sha256sum "$OUTPUT" > "$OUTPUT.sha256")
chmod 600 "$BACKUP_DIR/postgres-$STAMP.dump.age" "$BACKUP_DIR/postgres-$STAMP.dump.age.sha256"
printf '%s\n' "Encrypted backup created: $OUTPUT"
