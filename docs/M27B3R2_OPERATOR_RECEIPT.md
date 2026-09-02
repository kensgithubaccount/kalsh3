# M27B.3R2 smoke receipt

Use `scripts/run_m27b3_smoke_receipt.py` from the repository root for future
bounded smokes. It waits for the child without imposing a timeout, runs Python
with `-u` and `PYTHONUNBUFFERED=1`, and writes separate `stdout.log` and
`stderr.log` files plus `process-receipt.json` in the supplied unique run
directory.

The receipt is written atomically as `STARTING` before the child is spawned and
as `RUNNING` immediately after spawn. It records the wrapper and child PIDs,
the internally constructed reviewed command, expected and observed code/tree
identities, clean-state result, UTC start/end, exit code or terminating signal,
child resource usage, output hashes, and hashes for `universe.sqlite` and
`observations.sqlite` when those files exist. It records only the names of its
minimal environment allowlist, including the internal parent-watchdog binding,
never environment values. The wrapper does not select a host, add headers,
inspect response bodies, or change application authority.

The child watchdog checks that the recorded wrapper PID remains its parent on a
portable polling interval. If the wrapper is forcibly killed and cannot write a
terminal receipt, the stale `RUNNING` receipt remains visibly incomplete. The
read-only `inspect_receipt` helper may classify it as `INTERRUPTED` only after
both recorded processes are absent; it never rewrites the receipt and never
infers `COMPLETED` from database presence. Ordinary wrapper signals are
forwarded and reaped, and all post-spawn cleanup is bounded and fail-closed.

The target `run-dir` must not exist before invocation and must be a direct child
contained by the existing `parent-dir`. The expected SHA and tree are the
independently approved exact repository identities. The wrapper constructs the
reviewed runner command internally; the operator cannot supply another host,
cadence, iteration count, database path, authenticated argument, or arbitrary
child command. The resulting child command is exactly one unauthenticated
public-read smoke and does not authorize the 24-hour pilot.

Example:

```text
/Users/ksyme/miniforge3/envs/kalsh3/bin/python -u scripts/run_m27b3_smoke_receipt.py \
  --parent-dir /path/to/smoke-runs \
  --run-dir /path/to/smoke-runs/unique-run \
  --expected-code-sha e8c6faff5a72db6010fd4ae22713b0a0831b947e \
  --expected-tree 353aeba5d99c67c5baa4c72901965b323367ecbf \
  --python /Users/ksyme/miniforge3/envs/kalsh3/bin/python
```
