#!/usr/bin/env bash
set -euo pipefail

PROTOCOL_SHA="e62374c9db5b7f3355d413687ab82e868ca2686fbdd6fd9c57a1329151a8b40d"
RUN_ROOT="$HOME/cpi_e1_p11_prospective_20261014"
STATE="$HOME/cpi_p11_vm_scheduler_20261014"
RUNNER="$HOME/run_cpi_p11_frozen_vm.sh"
USER_NAME="$(id -un)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "$(uname -s)" != "Linux" ]; then
  echo "BLOCKER: CPI P11 systemd arming is Linux-only"
  exit 2
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "BLOCKER: systemd is required"
  exit 2
fi

if ! command -v sudo >/dev/null 2>&1; then
  echo "BLOCKER: sudo is required"
  exit 2
fi

cd "$REPO"

if [ "$(git branch --show-current)" != "main" ]; then
  echo "BLOCKER: arm only from canonical main"
  exit 2
fi

HEAD="$(git rev-parse HEAD)"
ORIGIN_MAIN="$(git rev-parse origin/main 2>/dev/null || true)"
if [ -z "$ORIGIN_MAIN" ] || [ "$HEAD" != "$ORIGIN_MAIN" ]; then
  echo "BLOCKER: local main must exactly equal origin/main"
  echo "HEAD=$HEAD"
  echo "origin/main=$ORIGIN_MAIN"
  exit 2
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "BLOCKER: tracked working tree must be clean"
  git status --short
  exit 2
fi

UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  echo "BLOCKER: uv is not on PATH"
  exit 2
fi
UV="$(readlink -f "$UV")"

SYNCED="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
if [ "$SYNCED" != "yes" ]; then
  echo "BLOCKER: VM clock is not NTP-synchronized"
  echo "NTPSynchronized=$SYNCED"
  exit 2
fi

mkdir -p "$STATE/units" "$STATE/logs"

SHORT="$(printf '%s' "$HEAD" | cut -c1-12)"
WORKTREE="$HOME/kalsh3_cpi_p11_vm_$SHORT"

if [ -e "$WORKTREE" ]; then
  if [ ! -d "$WORKTREE/.git" ] && [ ! -f "$WORKTREE/.git" ]; then
    echo "BLOCKER: expected worktree path exists but is not a git worktree"
    exit 2
  fi
  ACTUAL="$(git -C "$WORKTREE" rev-parse HEAD)"
  if [ "$ACTUAL" != "$HEAD" ]; then
    echo "BLOCKER: existing CPI P11 VM worktree is pinned to a different SHA"
    exit 2
  fi
else
  git worktree add --detach "$WORKTREE" "$HEAD"
fi

SPEC="$WORKTREE/docs/reviews/artifacts/cpi-p11-phase0-prospective-protocol/spec.json"
ACTUAL_PROTOCOL="$(python3 - "$SPEC" <<'PY'
import json
import sys
with open(sys.argv[1], "rb") as handle:
    print(json.load(handle)["spec_digest_sha256"])
PY
)"
if [ "$ACTUAL_PROTOCOL" != "$PROTOCOL_SHA" ]; then
  echo "BLOCKER: frozen protocol digest mismatch"
  exit 2
fi

"$UV" sync --locked --project "$WORKTREE"

cat > "$RUNNER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
WORKTREE="$WORKTREE"
UV="$UV"
ROOT="$RUN_ROOT"
COMMAND="${1:?command required}"
INPUT="${2:-}"

cd "$WORKTREE"

case "$COMMAND" in
  preflight|market|preclose)
    exec "$UV" run --locked --project "$WORKTREE" python scripts/run_cpi_e1_p11.py       --root "$ROOT" "$COMMAND"
    ;;
  truth-score)
    exec "$UV" run --locked --project "$WORKTREE" python scripts/run_cpi_e1_p11.py       --root "$ROOT" truth --score
    ;;
  record-reuters-pass|record-reuters-nonpass)
    if [ -z "$INPUT" ]; then
      echo "BLOCKER: Reuters receipt input path required"
      exit 2
    fi
    exec "$UV" run --locked --project "$WORKTREE" python scripts/run_cpi_e1_p11.py       --root "$ROOT" "$COMMAND" --input "$INPUT"
    ;;
  verify)
    exec "$UV" run --locked --project "$WORKTREE" python scripts/run_cpi_e1_p11.py       --root "$ROOT" verify
    ;;
  *)
    echo "BLOCKER: unsupported frozen runner command: $COMMAND"
    exit 2
    ;;
esac
EOF
chmod 700 "$RUNNER"

write_service() {
  local stage="$1"
  local command="$2"
  local service="kalsh3-cpi-p11-$stage.service"
  cat > "$STATE/units/$service" <<EOF
[Unit]
Description=Kalsh3 CPI-E1-P11 $stage

[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$WORKTREE
Environment=HOME=$HOME
ExecStart=$RUNNER $command
StandardOutput=append:$STATE/logs/$stage.stdout.log
StandardError=append:$STATE/logs/$stage.stderr.log
EOF
}

write_timer() {
  local stage="$1"
  shift
  local timer="kalsh3-cpi-p11-$stage.timer"
  local service="kalsh3-cpi-p11-$stage.service"
  {
    echo "[Unit]"
    echo "Description=Timer for Kalsh3 CPI-E1-P11 $stage"
    echo
    echo "[Timer]"
    for when in "$@"; do
      echo "OnCalendar=$when"
    done
    echo "AccuracySec=1s"
    echo "RandomizedDelaySec=0"
    echo "Persistent=false"
    echo "Unit=$service"
    echo
    echo "[Install]"
    echo "WantedBy=timers.target"
  } > "$STATE/units/$timer"
}

write_service preflight preflight
write_timer preflight "2026-10-14 11:45:00 UTC"

write_service market market
write_timer market "2026-10-14 12:05:00 UTC"

write_service preclose preclose
write_timer preclose "2026-10-14 12:20:00 UTC"

write_service truth truth-score
write_timer truth   "2026-10-14 12:35:00 UTC"   "2026-10-14 12:45:00 UTC"   "2026-10-14 13:00:00 UTC"   "2026-10-14 14:00:00 UTC"   "2026-10-14 16:00:00 UTC"

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

for unit in "${UNITS[@]}"; do
  sudo install -o root -g root -m 0644 "$STATE/units/$unit" "/etc/systemd/system/$unit"
done

sudo systemctl daemon-reload
sudo systemctl disable --now   kalsh3-cpi-p11-preflight.timer   kalsh3-cpi-p11-market.timer   kalsh3-cpi-p11-preclose.timer   kalsh3-cpi-p11-truth.timer >/dev/null 2>&1 || true

sudo systemctl enable --now   kalsh3-cpi-p11-preflight.timer   kalsh3-cpi-p11-market.timer   kalsh3-cpi-p11-preclose.timer   kalsh3-cpi-p11-truth.timer

{
  echo "armed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "runtime_head=$HEAD"
  echo "runtime_tree=$(git -C "$WORKTREE" rev-parse HEAD^{tree})"
  echo "worktree=$WORKTREE"
  echo "uv=$UV"
  echo "uv_version=$("$UV" --version | tr ' ' '-')"
  echo "protocol_sha256=$PROTOCOL_SHA"
  echo "runner_sha256=$(sha256sum "$RUNNER" | awk '{print $1}')"
  echo "ntp_synchronized=$SYNCED"
  for unit in "${UNITS[@]}"; do
    echo "$unit.sha256=$(sha256sum "$STATE/units/$unit" | awk '{print $1}')"
  done
} > "$STATE/arming_receipt.txt"

echo "=== CPI P11 VM ARMED ==="
cat "$STATE/arming_receipt.txt"
echo
echo "=== SYSTEMD TIMERS ==="
systemctl list-timers --all 'kalsh3-cpi-p11-*' --no-pager
echo
echo "=== UNIT STATUS ==="
for timer in   kalsh3-cpi-p11-preflight.timer   kalsh3-cpi-p11-market.timer   kalsh3-cpi-p11-preclose.timer   kalsh3-cpi-p11-truth.timer
do
  echo "--- $timer ---"
  systemctl show "$timer"     -p ActiveState     -p UnitFileState     -p NextElapseUSecRealtime     -p LastTriggerUSec
done
