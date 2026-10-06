#!/bin/zsh
set -euo pipefail

PROTOCOL_SHA="e62374c9db5b7f3355d413687ab82e868ca2686fbdd6fd9c57a1329151a8b40d"
RUN_ROOT="$HOME/cpi_e1_p11_prospective_20261014"
STATE="$HOME/cpi_p11_scheduler_20261014"
AGENTS="$HOME/Library/LaunchAgents"
RUNNER="$HOME/run_cpi_p11_frozen.sh"
UIDN="$(id -u)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "BLOCKER: CPI P11 launchd arming is macOS-only"
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
  exit 2
fi

UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  echo "BLOCKER: uv is not on PATH"
  exit 2
fi
UV="$(cd "$(dirname "$UV")" && pwd)/$(basename "$UV")"

if [ "$(date +%z)" != "-0400" ]; then
  echo "BLOCKER: launchd calendar is local-time based; system must be on EDT (-0400)"
  echo "current zone: $(date +%Z) $(date +%z)"
  exit 2
fi

mkdir -p "$STATE/logs" "$STATE/plists" "$AGENTS"

SHORT="$(printf '%s' "$HEAD" | cut -c1-12)"
WORKTREE="$HOME/kalsh3_cpi_p11_$SHORT"

if [ -e "$WORKTREE" ]; then
  if [ ! -d "$WORKTREE/.git" ] && [ ! -f "$WORKTREE/.git" ]; then
    echo "BLOCKER: expected worktree path exists but is not a git worktree"
    exit 2
  fi
  ACTUAL="$(git -C "$WORKTREE" rev-parse HEAD)"
  if [ "$ACTUAL" != "$HEAD" ]; then
    echo "BLOCKER: existing CPI P11 worktree is pinned to a different SHA"
    exit 2
  fi
else
  git worktree add --detach "$WORKTREE" "$HEAD"
fi

SPEC="$WORKTREE/docs/reviews/artifacts/cpi-p11-phase0-prospective-protocol/spec.json"
ACTUAL_PROTOCOL="$(
  "$UV" run --locked --project "$WORKTREE" python -c   'import json,sys; print(json.load(open(sys.argv[1]))["spec_digest_sha256"])' "$SPEC"
)"
if [ "$ACTUAL_PROTOCOL" != "$PROTOCOL_SHA" ]; then
  echo "BLOCKER: frozen protocol digest mismatch"
  exit 2
fi

cat > "$RUNNER" <<EOF
#!/bin/zsh
set -euo pipefail
WORKTREE="$WORKTREE"
UV="$UV"
ROOT="$RUN_ROOT"
COMMAND="\${1:?command required}"
INPUT="\${2:-}"

cd "$WORKTREE"

case "\$COMMAND" in
  preflight|market|preclose)
    exec "$UV" run --locked python scripts/run_cpi_e1_p11.py \
      --root "\$ROOT" "\$COMMAND"
    ;;
  truth-score)
    exec "$UV" run --locked python scripts/run_cpi_e1_p11.py \
      --root "\$ROOT" truth --score
    ;;
  record-reuters-pass|record-reuters-nonpass)
    if [ -z "\$INPUT" ]; then
      echo "BLOCKER: Reuters receipt input path required"
      exit 2
    fi
    exec "$UV" run --locked python scripts/run_cpi_e1_p11.py \
      --root "\$ROOT" "\$COMMAND" --input "\$INPUT"
    ;;
  *)
    echo "BLOCKER: unsupported frozen runner command: \$COMMAND"
    exit 2
    ;;
esac
EOF
chmod 700 "$RUNNER"

xml_escape() {
  printf '%s' "$1" | sed     -e 's/&/\&amp;/g'     -e 's/</\&lt;/g'     -e 's/>/\&gt;/g'     -e 's/"/\&quot;/g'     -e "s/'/\&apos;/g"
}

write_job() {
  local label="$1"
  local command="$2"
  local hour="$3"
  local minute="$4"
  local plist="$AGENTS/$label.plist"
  local runner_xml root_xml out_xml err_xml

  runner_xml="$(xml_escape "$RUNNER")"
  root_xml="$(xml_escape "$WORKTREE")"
  out_xml="$(xml_escape "$STATE/logs/$label.stdout.log")"
  err_xml="$(xml_escape "$STATE/logs/$label.stderr.log")"

  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$runner_xml</string>
    <string>$command</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$root_xml</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Month</key><integer>10</integer>
    <key>Day</key><integer>14</integer>
    <key>Hour</key><integer>$hour</integer>
    <key>Minute</key><integer>$minute</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$out_xml</string>
  <key>StandardErrorPath</key>
  <string>$err_xml</string>
</dict>
</plist>
EOF
  plutil -lint "$plist" >/dev/null
  cp "$plist" "$STATE/plists/"
}

write_truth_job() {
  local label="com.kalsh3.cpi.p11.truth"
  local plist="$AGENTS/$label.plist"
  local runner_xml root_xml out_xml err_xml

  runner_xml="$(xml_escape "$RUNNER")"
  root_xml="$(xml_escape "$WORKTREE")"
  out_xml="$(xml_escape "$STATE/logs/$label.stdout.log")"
  err_xml="$(xml_escape "$STATE/logs/$label.stderr.log")"

  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$runner_xml</string>
    <string>truth-score</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$root_xml</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Month</key><integer>10</integer><key>Day</key><integer>14</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>35</integer></dict>
    <dict><key>Month</key><integer>10</integer><key>Day</key><integer>14</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Month</key><integer>10</integer><key>Day</key><integer>14</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Month</key><integer>10</integer><key>Day</key><integer>14</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Month</key><integer>10</integer><key>Day</key><integer>14</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>$out_xml</string>
  <key>StandardErrorPath</key>
  <string>$err_xml</string>
</dict>
</plist>
EOF
  plutil -lint "$plist" >/dev/null
  cp "$plist" "$STATE/plists/"
}

write_caffeinate_job() {
  local label="com.kalsh3.cpi.p11.caffeinate"
  local plist="$AGENTS/$label.plist"
  local out_xml err_xml
  out_xml="$(xml_escape "$STATE/logs/$label.stdout.log")"
  err_xml="$(xml_escape "$STATE/logs/$label.stderr.log")"

  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>-s</string>
    <string>-t</string>
    <string>21600</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Month</key><integer>10</integer>
    <key>Day</key><integer>14</integer>
    <key>Hour</key><integer>7</integer>
    <key>Minute</key><integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$out_xml</string>
  <key>StandardErrorPath</key>
  <string>$err_xml</string>
</dict>
</plist>
EOF
  plutil -lint "$plist" >/dev/null
  cp "$plist" "$STATE/plists/"
}

write_job "com.kalsh3.cpi.p11.preflight" "preflight" 7 45
write_job "com.kalsh3.cpi.p11.market" "market" 8 5
write_job "com.kalsh3.cpi.p11.preclose" "preclose" 8 20
write_truth_job
write_caffeinate_job

LABELS=(
  com.kalsh3.cpi.p11.caffeinate
  com.kalsh3.cpi.p11.preflight
  com.kalsh3.cpi.p11.market
  com.kalsh3.cpi.p11.preclose
  com.kalsh3.cpi.p11.truth
)

for label in "${LABELS[@]}"; do
  plist="$AGENTS/$label.plist"
  launchctl bootout "gui/$UIDN" "$plist" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$UIDN" "$plist"
done

{
  echo "armed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "runtime_head=$HEAD"
  echo "runtime_tree=$(git -C "$WORKTREE" rev-parse HEAD^{tree})"
  echo "worktree=$WORKTREE"
  echo "uv=$UV"
  echo "protocol_sha256=$PROTOCOL_SHA"
  echo "runner_sha256=$(shasum -a 256 "$RUNNER" | awk '{print $1}')"
  echo "timezone=$(date +%Z)"
  echo "utc_offset=$(date +%z)"
  for label in "${LABELS[@]}"; do
    plist="$AGENTS/$label.plist"
    echo "$label.plist.sha256=$(shasum -a 256 "$plist" | awk '{print $1}')"
  done
} > "$STATE/arming_receipt.txt"

echo "=== CPI P11 ARMED ==="
cat "$STATE/arming_receipt.txt"
echo
echo "=== LAUNCHD JOBS ==="
for label in "${LABELS[@]}"; do
  echo "--- $label ---"
  launchctl print "gui/$UIDN/$label" 2>&1 |     grep -E 'state =|runs =|last exit code|Hour|Minute|Day|Month' || true
done
echo
echo "IMPORTANT: keep the Mac awake, lid open, plugged in, and on EDT through the run."
echo "IMPORTANT: these Month/Day LaunchAgents are annual until cleanup; remove them after P11."
