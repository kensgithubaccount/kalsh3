#!/bin/zsh
set -euo pipefail

STATE="$HOME/cpi_p11_scheduler_20261014"
AGENTS="$HOME/Library/LaunchAgents"
UIDN="$(id -u)"

LABELS=(
  com.kalsh3.cpi.p11.caffeinate
  com.kalsh3.cpi.p11.preflight
  com.kalsh3.cpi.p11.market
  com.kalsh3.cpi.p11.preclose
  com.kalsh3.cpi.p11.truth
)

mkdir -p "$STATE/cleanup_archive"

for label in "${LABELS[@]}"; do
  plist="$AGENTS/$label.plist"
  if [ -f "$plist" ]; then
    cp "$plist" "$STATE/cleanup_archive/"
    launchctl print "gui/$UIDN/$label"       > "$STATE/cleanup_archive/$label.launchctl.txt" 2>&1 || true
    launchctl bootout "gui/$UIDN" "$plist" >/dev/null 2>&1 || true
    rm -f "$plist"
  fi
done

echo "=== VERIFY CPI P11 JOBS REMOVED ==="
for label in "${LABELS[@]}"; do
  if launchctl print "gui/$UIDN/$label" >/dev/null 2>&1; then
    echo "FAIL: still loaded $label"
    exit 1
  fi
  if [ -e "$AGENTS/$label.plist" ]; then
    echo "FAIL: plist remains $label"
    exit 1
  fi
  echo "PASS: removed $label"
done

echo "cleanup_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"   > "$STATE/cleanup_receipt.txt"
echo "Evidence root was not modified."
