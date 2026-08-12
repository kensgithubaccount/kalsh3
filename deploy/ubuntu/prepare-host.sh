#!/bin/sh
set -eu

SYSCTL_FILE=${KPV3_SYSCTL_FILE:-/etc/sysctl.d/60-kalshi-v3-redis.conf}
SETTING='vm.overcommit_memory = 1'

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this host prerequisite as root (for example: sudo $0)." >&2
    exit 1
fi

if [ ! -f "$SYSCTL_FILE" ] || [ "$(cat "$SYSCTL_FILE")" != "$SETTING" ]; then
    printf '%s\n' "$SETTING" > "$SYSCTL_FILE"
fi

sysctl -w vm.overcommit_memory=1 >/dev/null
actual=$(sysctl -n vm.overcommit_memory)
if [ "$actual" != "1" ]; then
    echo "vm.overcommit_memory verification failed: got $actual" >&2
    exit 1
fi

echo "Redis host prerequisite active and persistent in $SYSCTL_FILE."
