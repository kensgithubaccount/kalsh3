#!/bin/sh
# CI tests only: the runner agent and dependency setup stay outside this namespace.
set -eu
task_uid="$(id -u)"
task_gid="$(id -g)"
task_path="$PATH"
exec sudo --preserve-env=HOME,UV_CACHE_DIR,KALSH3_TEST_POSTGRES_DSN \
  unshare --net -- /bin/sh -c '
    set -eu
    task_uid="$1"
    task_gid="$2"
    task_path="$3"
    shift 3
    ip link set lo up
    ip -brief address
    test -z "$(ip route show)"
    test -z "$(ip -6 route show default)"
    exec setpriv --reuid="$task_uid" --regid="$task_gid" --init-groups \
      env PATH="$task_path" "$@"
  ' wn-offline-tests "$task_uid" "$task_gid" "$task_path" "$@"
