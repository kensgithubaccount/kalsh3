#!/usr/bin/env bash
set -euo pipefail

STATE="$HOME/cpi_p11_vm_scheduler_20261014"

if [ "$(uname -s)" != "Linux" ]; then
  echo "BLOCKER: CPI P11 systemd cleanup is Linux-only"
  exit 2
fi

UNITS=(
  kalsh3-cpi-p11-preflight.service
  kalsh3-cpi-p11-preflight.timer
  kalsh3-cpi-p11-market.service
  kalsh3-cpi-p11-market.timer
  kalsh3-cpi-p11-preclose.service
  kalsh3-cpi-p11-preclose.timer
  kalsh3-cpi-p11-truth.service
  kalsh3-cpi-p11-truth.timer
)

mkdir -p "$STATE/cleanup_archive"

for unit in "${UNITS[@]}"; do
  systemctl status "$unit" --no-pager     > "$STATE/cleanup_archive/$unit.status.txt" 2>&1 || true
  if [ -f "/etc/systemd/system/$unit" ]; then
    sudo cp "/etc/systemd/system/$unit" "$STATE/cleanup_archive/$unit"
  fi
done

sudo systemctl disable --now   kalsh3-cpi-p11-preflight.timer   kalsh3-cpi-p11-market.timer   kalsh3-cpi-p11-preclose.timer   kalsh3-cpi-p11-truth.timer >/dev/null 2>&1 || true

for unit in "${UNITS[@]}"; do
  sudo rm -f "/etc/systemd/system/$unit"
done

sudo systemctl daemon-reload
sudo systemctl reset-failed >/dev/null 2>&1 || true

echo "=== VERIFY CPI P11 VM TIMERS REMOVED ==="
for timer in   kalsh3-cpi-p11-preflight.timer   kalsh3-cpi-p11-market.timer   kalsh3-cpi-p11-preclose.timer   kalsh3-cpi-p11-truth.timer
do
  if systemctl list-unit-files "$timer" --no-pager | grep -q "$timer"; then
    echo "FAIL: timer still installed: $timer"
    exit 1
  fi
  echo "PASS: removed $timer"
done

echo "cleanup_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"   > "$STATE/cleanup_receipt.txt"
echo "Scientific evidence root was not modified."
