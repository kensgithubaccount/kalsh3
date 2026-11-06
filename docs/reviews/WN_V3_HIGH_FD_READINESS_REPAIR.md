# WeatherNext V3 high-FD readiness repair

OFFLINE ENGINEERING ONLY. Parent: Issue #234. Separate from PR267/268.
Baseline: 3cd2695592e10132ed54773ccb9d0fbfbcbe635f.

The reviewed V3 implementation used select.select on a valid pipe FD2753 and
returned HOST_FAILURE because select rejected the descriptor number. The repair
uses DefaultSelector, registers the existing nonblocking incoming pipe once, and
waits with the unchanged min(0.01, remaining_absolute_deadline) ceiling. No timeout,
frame/ACK protocol, byte limit, HTTP rejection, worker ownership or authority flag
is relaxed. Selector allocation/registration/poll failures remain negative.

Selector ownership begins inside the existing protected try. A nested finally
closes it before existing worker terminate/kill/join and connection cleanup. A
close OSError/ValueError makes the result HOST_FAILURE; cleanup still runs.
Other exceptions/interrupts propagate after that cleanup. Close-fault tests label
the actual injected boundary: failure AFTER releasing the real selector. They do
not claim an OS resource is closed when its close syscall has not released it.

Deterministic tests duplicate an actual pipe read handle with F_DUPFD_CLOEXEC to
at least1024/2753. This avoids GC timing and avoids occupying arbitrary existing
FDs. The existing low handle is closed; the same real pipe/worker is used.
Unsupported descriptor allowance yields HIGH_FD_ENVIRONMENT_UNSUPPORTED, a failed
engineering check with NOT TESTED in its message; it is never an untested PASS or
silent skip. No host limit is raised.

New regressions check one registration, finite poll ceiling, healthy native/high
behavior, allocation/register/poll/close failures, no-child admission exits,
startup failure, repeated descriptors, signal interruption and reaping. Existing
V3 tests remain byte-for-byte unchanged. The high-FD suite helper runs all76
original cases with real read handles >=2753, covering cancellation, deadlines,
clock rollback, malformed IPC, status/body/header caps and interrupted startup.
Relocation applies to in-process context.Pipe calls. Tests starting independent
Python subprocesses retain their original startup and execute unchanged.
It prints observed minimum/count and exits nonzero if coverage was not exercised.
The frozen baseline control must reproduce36 failures/40 passes; its expected
negative result is separate from repaired candidate assertions, which must pass.

All Python test phases in CI execute inside an empty Linux network namespace
with loopback only and no external route, under the runner UID. Tool/dependency
setup and GitHub artifact transfer stay outside. PostgreSQL16 exposes its local
Unix socket through a fixed service bind mount; tests use that socket from inside
the same denied namespace, with no network forwarding. Redis/NATS runtime smoke
remains in its existing internal Docker networks. These CI-only fixtures do not
provide a WeatherNext/Kalshi source client or a real account credential.

Local reproduction with existing installed dependencies:
```sh
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' \
  python -m pytest -q tests/test_wn_host_high_fd_repair.py tests/test_wn_host_feasibility_v3.py
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' \
  python scripts/wn_v3_high_fd_suite.py
```

This engineering repair does not prove live DNS/TLS enforcement, source custody,
forecast-member availability, cost feasibility, scientific scoring, probability
quality, edge, profitability or production readiness. Deferred SQLite connection
ownership and exact historical runner-image causality remain distinct issues.
No frozen evidence or historical review receipt is rewritten. The V3 source copy
is the one explicitly authorized implementation change; historical copies/seals
remain unchanged.

No merge during this task. Require all4 exact-head CI jobs and a fresh independent
adversarial verdict before recommending any later merge.

The first exact-head CI passed verification, runtime smoke and PostgreSQL tests,
but Trivy rejected the existing optional oauthlib4.0.0 lock record for
CVE-2026-49272. The narrowly scoped security refresh updates only that package's
lock record to the upstream4.0.1 fix; other package records and the lock revision
are preserved. No scanner suppression or source authentication is enabled. This
changes the new candidate lock, not any historical lock/evidence receipt.
LIVE NOT AUTHORIZED. SCIENTIFIC SCORING NOT AUTHORIZED. TRADING AUTHORITY NONE.
