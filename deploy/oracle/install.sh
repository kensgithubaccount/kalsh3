#!/bin/sh
set -eu
umask 077

STATE_DIR=${KPV3_STATE_DIR:-/var/lib/kalshi-v3}
SECRETS_DIR=${KPV3_SECRETS_DIR:-./secrets}
mkdir -p "$STATE_DIR" "$SECRETS_DIR" backups
chmod 700 "$STATE_DIR" "$SECRETS_DIR" backups

# Idempotent installer: existing secrets are deliberately never replaced.
if [ ! -s "$STATE_DIR/vault.key" ]; then
    openssl rand 32 > "$STATE_DIR/vault.key"
fi
if [ ! -s "$SECRETS_DIR/postgres_password" ]; then
    openssl rand -base64 32 > "$SECRETS_DIR/postgres_password"
fi
if [ ! -s "$STATE_DIR/setup.token" ]; then
    openssl rand -base64 32 > "$STATE_DIR/setup.token"
fi
chmod 600 "$STATE_DIR"/* "$SECRETS_DIR"/*
chown -R 10001:10001 "$STATE_DIR"
echo "Persistent secrets initialized. Retrieve the setup token directly from $STATE_DIR/setup.token."
echo "Configure KPV3_HOSTNAME, then run: docker compose up -d"
