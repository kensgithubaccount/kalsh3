#!/bin/sh
# Restore only into an isolated disposable database with production networking disabled.
set -eu
umask 077

if [ "$#" -ne 1 ]; then
  echo "usage: restore-drill.sh ENCRYPTED_BACKUP" >&2
  exit 64
fi
BACKUP=$1
AGE_IDENTITY_FILE=${KPV3_AGE_IDENTITY_FILE:-/run/secrets/backup_age_identity}
TMP=$(mktemp -d)
NAME=kpv3-restore-drill-$$
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT HUP INT TERM

test -f "$BACKUP"
test -f "$BACKUP.sha256"
test -s "$AGE_IDENTITY_FILE"
(cd "$(dirname "$BACKUP")" && sha256sum -c "$(basename "$BACKUP").sha256")
age -d -i "$AGE_IDENTITY_FILE" -o "$TMP/postgres.dump" "$BACKUP"

# --network none proves the drill cannot reach Kalshi or the production database.
docker run -d --name "$NAME" --network none -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine
until docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
docker cp "$TMP/postgres.dump" "$NAME:/tmp/postgres.dump"
docker exec "$NAME" createdb -U postgres restore_drill
docker exec "$NAME" pg_restore -U postgres -d restore_drill --exit-on-error /tmp/postgres.dump
docker exec "$NAME" psql -U postgres -d restore_drill -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')" \
  | awk '$1 > 0 { ok=1 } END { exit !ok }'
printf '%s\n' "Restore drill passed in isolated network namespace"
