#!/usr/bin/env bash
# No collector, request, retry, source edit, timeout override or external test network.
set -euo pipefail
ROOT="$PWD"
OUT="$ROOT/diagnostic-results"
mkdir "$OUT"
git rev-parse HEAD HEAD^{tree} > "$OUT/diagnostic-identity.txt"
env | sort | sed -n '/^ImageOS=/p; /^ImageVersion=/p; /^RUNNER_OS=/p; /^RUNNER_ARCH=/p' > "$OUT/image.txt"
uname -a >> "$OUT/image.txt"
id >> "$OUT/image.txt"
"$ROOT/.venv/bin/python" -VV > "$OUT/python.txt"
uv pip freeze > "$OUT/packages.txt"
ulimit -a > "$OUT/limits.txt"
RUNNER_UID="$(id -u)"
RUNNER_GID="$(id -g)"
# A new empty network namespace has no external interface or route. Preserve UID.
# Loopback permits unchanged local-only unit tests; all source access is absent.
cat > "$OUT/namespace.sh" <<'NAMESPACE'
#!/usr/bin/env bash
set -euo pipefail
ip link set lo up
ip -brief address
ip route
exec setpriv --reuid="$1" --regid="$2" --init-groups env PYTHONPATH="$3" WN_DIAGNOSTIC_OUTPUT="$4" "${@:5}"
NAMESPACE
chmod +x "$OUT/namespace.sh"
sudo unshare --net -- bash "$OUT/namespace.sh" "$RUNNER_UID" "$RUNNER_GID" \
  "$ROOT/diagnostics/pr268" "$OUT/preflight.json" /bin/true > "$OUT/namespace-preflight.log" 2>&1
for PAIR in \
  pr268:5fef4fb384b7173476f75b315b92114fdf5287e6 \
  base:3cd2695592e10132ed54773ccb9d0fbfbcbe635f \
  passing:cbed8119f6505fd9d2fbe9c70595ee2019ae7893
do
  LABEL="${PAIR%%:*}"
  SHA="${PAIR#*:}"
  TARGET="$RUNNER_TEMP/pr268-control-$LABEL"
  git worktree add --detach "$TARGET" "$SHA"
  (
    cd "$TARGET"
    git rev-parse HEAD HEAD^{tree} > "$OUT/$LABEL-identity.txt"
    git status --porcelain > "$OUT/$LABEL-before-status.txt"
    sha256sum services/forecasting/wn_host_simulation_v3.py tests/test_wn_host_feasibility_v3.py > "$OUT/$LABEL-source-sha256.txt"
    sudo unshare --net -- bash "$OUT/namespace.sh" "$RUNNER_UID" "$RUNNER_GID" \
      "$ROOT/diagnostics/pr268" "$OUT/$LABEL-import.json" \
      "$ROOT/.venv/bin/python" -c 'import hashlib, pathlib; import services.forecasting.wn_host_simulation_v3 as h; p=pathlib.Path(h.__file__); print(p.resolve()); print(hashlib.sha256(p.read_bytes()).hexdigest())' \
      > "$OUT/$LABEL-import.txt" 2>&1
    for MODE in isolated-untraced isolated-traced full-untraced full-traced
    do
      ARGS=(-q)
      if [[ "$MODE" == isolated-* ]]; then
        ARGS+=(tests/test_wn_host_feasibility_v3.py::test_incremental_stream_exact_caps_and_reaped_worker)
      fi
      if [[ "$MODE" == *-traced ]]; then
        ARGS+=(-p host_exception_plugin)
      fi
      printf '%s\n' "$ROOT/.venv/bin/pytest ${ARGS[*]}" > "$OUT/$LABEL-$MODE-command.txt"
      set +e
      sudo unshare --net -- bash "$OUT/namespace.sh" "$RUNNER_UID" "$RUNNER_GID" \
        "$ROOT/diagnostics/pr268" "$OUT/$LABEL-$MODE.json" \
        "$ROOT/.venv/bin/pytest" "${ARGS[@]}" > "$OUT/$LABEL-$MODE.log" 2>&1
      RESULT=$?
      set -e
      printf '%s\n' "$RESULT" > "$OUT/$LABEL-$MODE-exit.txt"
      if [ "$RESULT" -gt 1 ]; then
        echo "Unexpected pytest or namespace error: $LABEL $MODE" >&2
        exit 2
      fi
      if [[ "$MODE" == *-traced ]]; then
        test -s "$OUT/$LABEL-$MODE.json"
      fi
      rg '[0-9]+ (passed|failed)' "$OUT/$LABEL-$MODE.log" > /dev/null
    done
    git status --porcelain > "$OUT/$LABEL-after-status.txt"
  )
done
# Observer self-tests also execute with external networking absent.
sudo unshare --net -- bash "$OUT/namespace.sh" "$RUNNER_UID" "$RUNNER_GID" \
  "$ROOT/diagnostics/pr268" "$OUT/observer.json" \
  "$ROOT/.venv/bin/pytest" -q "$ROOT/diagnostics/pr268/test_observer.py" > "$OUT/observer.log" 2>&1
sha256sum "$OUT"/* > "$OUT/SHA256SUMS.txt"

