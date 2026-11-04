# WeatherNext host-integration feasibility V3 — offline only

Parent Issue #234. Baseline `975d728ae4bc40afff8b830b64082e06a2b03286`.
Required post-merge run `41871571758` completed all four jobs successfully at
that exact SHA before implementation began. This additive gate preserves all
eight PR #264/#265 files byte-for-byte. It enables no source acquisition,
scheduler, scoring, probability model, account access or production integration.

## Generated worker and parent watchdog

`wn_host_simulation_v3` accepts a bounded `GeneratedScenario` recipe, never a
URL, filename, callback, credential or client. A spawn child executes a fixed
script with simulated DNS, TLS, headers and body phases. The parent starts one
absolute monotonic deadline before spawn, capped by the registry total deadline.
Every phase, chunk, cancellation and worker-exit wait uses its remaining time.
Progress cannot renew a deadline. Expiration or cancellation rejects completion.
An independent cleanup allowance defaults to 500 ms, capped at 2 seconds.

The child and parent communicate through two anonymous one-way OS pipes.
There is no socket transport. The child sends bounded length-prefixed messages
and waits for a one-byte acknowledgement before sending another. The parent
uses nonblocking `os.read` plus `select`, parses incrementally and rejects
zero/oversized framing. It never calls a potentially blocking message receive
after mere readiness of a partial header. A deliberately partial IPC frame
is terminated at the original deadline. ACKs cannot accumulate because only
one message is outstanding.

Raw response buffers retain at most 16 KiB headers and 64 KiB body. Every
append checks remaining capacity first. IPC buffering is bounded to 1,029
bytes; generated chunk payloads are at most 1,024 bytes. A single overflow
byte triggers termination and preserves the capped prefix as incomplete.
Recipe input sizes are bounded too. This proves bounded buffering for this
specific generated IPC protocol, not for an unimplemented HTTP library.

The parent requests termination, waits at most 50 ms, escalates to process
kill, and bounds its remaining join allowance. It records PID, monotonic
start/stop, last phase, cleanup/reaping state, force-kill use, raw prefixes,
status/completeness and peak IPC buffer. Unreaped workers fail closed. Both
parent monotonic rollback detection and explicit synthetic rollback markers
produce negative outcomes. No worker-supplied timestamp establishes host time.
UTC synchronization and uncertainty are not attested here.

Generated blocking workers also poll parent identity and exit after parent
death. This cooperative orphan behavior is tested with actual parent kill;
it does not establish orphan containment for an arbitrary blocked native
resolver/TLS call. Process startup, filesystem calls, OS scheduling and
uninterruptible kernel states are not hard real-time bounded by this code.
Those limitations must remain visible in any subsequent host-integration gate.

## Fixed execution registry and raw journals

`wn_host_registry_v3` uses one fixed directory name derived from the complete
frozen roster digest inside an operator-owned physical research parent. There
is no caller-selected execution ID or ticker/URL expansion. A different
cutoff or timeout budget cannot obtain a second execution under the same root.
Different physical research parents are distinct test sandboxes, not a claim
of machine-wide/global execution deduplication. Any later live registry root
must itself be frozen, access-controlled and independently reviewed.

A create-once claim binds the exact compiled 85 requests, cutoff bytes and
digest, policy digest, frozen roster, start clock and bounded budgets. Requests
are exactly the unchanged fixed-origin GET cutoff plus 84 frozen ticker paths,
with fixed public headers. Each slot is revalidated at the execution boundary.
The first generated cutoff body must match the claim bytes. A wrong request,
swapped/duplicate/missing slot, conflict or early start halts the execution.
Starts obey the unchanged three-second interval, requests are sequential and
the maximum remains 85. There are no redirects, retries, fallback routes or
account endpoints. All HTTP non-200 results, including 403/429, globally stop
the execution. Response Location and Retry-After never trigger actions.

An in-process reentrant lock serializes run/stop/close; atomic directory creation
arbitrates independent processes. Every slot durably publishes intent before
launching its generated worker, then exact raw frame and hash-chain commit.
Writes are exclusive, no-follow, short-write aware, file-fsynced, published by
create-once hardlink and directory-fsynced. Failures poison the writer and
preserve partials. Root inode identity is checked. No resume or repair API exists.
The separate V3 raw format preserves genuine header/body incompleteness rather
than relabeling a capped prefix as a complete V2 response.

Read-only recovery bounds files and bytes, rejects unexpected files, symlinks,
orphan partials, gaps, changed claims, intents, digests, chains and ledgers.
It always returns 60 negative city-day rows and preserves 12 clusters, five
October 4 failures, 59 abstentions and 14 unverified settlement families. The
frozen 19 previous 429 references remain unchanged. Missing final evidence
stays incomplete; disk failure can prevent durable negative-ledger publication,
so in-memory recovery is not described as a successful durable write.

## Verification and limitations

Focused reproduction with existing local dependencies:

```sh
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' /Users/ksyme/miniforge3/envs/kalsh3/bin/python -m pytest -p no:cacheprovider tests/test_wn_host_feasibility_v3.py tests/test_wn_settlement_boundary_v2.py tests/test_wn_october_settlement_recovery.py -q
mypy --strict --python-executable /Users/ksyme/miniforge3/envs/kalsh3/bin/python services/forecasting/wn_host_simulation_v3.py services/forecasting/wn_host_registry_v3.py
ruff check services/forecasting/wn_host_simulation_v3.py services/forecasting/wn_host_registry_v3.py tests/test_wn_host_feasibility_v3.py
```

The OS network denial is inherited by workers and subprocess probes. Focused
tests additionally deny Python DNS/connect operations. CI runs the same
generated scenarios with no source client; ordinary CI infrastructure itself
is not evidence of OS-wide network denial. No live Kalshi request is needed
or authorized to validate this gate.

Adversarial cases cover phase hangs, ignored SIGTERM and force kill, partial/
oversized IPC, delays and incremental streaming without deadline renewal,
cancellation, expired total budget, worker/parent exit, parent clock rollback,
headers/body limits, 403/429/redirect global halt, fixed request mutations,
claim races, repeated/threaded invocation, partial writes, disk/fsync/link
failures and altered/missing evidence. All outputs are research-only, even
HTTP 200 and byte-integrity matches. Independent review and full exact-head CI
are distinct required gates, never inferred from local tests alone.

No real DNS, TLS, HTTP framing, provider limits, service availability, host
clock sync, power-loss durability, source first-acquisition custody or actual
settlement admissibility is proven. Mode 0444 and local hashes are not
third-party immutability; a privileged owner can forge a self-consistent
registry. Live transport and scientific admission each require separate
authorization and independent evidence. No scientific edge, paper profitability,
production or trading authority follows from an offline PASS.
