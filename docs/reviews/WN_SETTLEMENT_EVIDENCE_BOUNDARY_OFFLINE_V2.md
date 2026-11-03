# WeatherNext settlement evidence boundary V2 — offline engineering only

Parent: Issue #234. Baseline: `b9a6cf6105c9fbf23fa5c32743e8c622c56b4202`,
the PR #264 merge. This additive candidate does not change the V1 planner,
frozen roster, October protocol, evaluator, manifests or historical evidence.
It provides local filesystem experiments and pure simulation contracts.
There is no HTTP client, acquisition entry point, scheduler, retry, scorer,
credential loader or runtime integration. **Live requests are not authorized.**

## Immutable request contract

`wn_settlement_safety_v2.RequestPolicy` derives slot 0 (the exact cutoff path)
and, only after valid cutoff bytes, the 84 individual frozen ticker paths from
the unchanged planner. The exact request tuple is checked: GET, fixed HTTPS
origin, singular path, ticker identity, and the two fixed public headers.
Slots cannot be swapped. No user URL, alternate port, userinfo, account path,
query, credential, redirect, route fallback or retry is accepted. The cutoff
strict comparison and equality refusal are inherited without amendment.
Maximum hypothetical budget is 85 requests, one active request, starts at
least 3 seconds apart, no retries, per-request 10 seconds, total 900 seconds.

These are contracts for a future separately approved adapter. This module
does not send requests. Checks must be repeated at any future actual sink;
calling a policy object is not proof that a different client obeys it.

## Create-once claim and journal publication

`OfflineJournal.create` requires a new physical absolute path. It walks parent
components through no-follow directory descriptors, reserves the root with
atomic mkdir (0700), and fsyncs the parent. A durable claim binds the roster
digest, 84 tickers, 85 slots and negative authority before returning a writer.
An existing root always refuses a new writer. No resume, overwrite, deletion,
repair or replacement-claim API exists. The root inode is checked against its
parent name before and after publication. The private descriptor is retained.

Each publication creates an exclusive no-follow `.partial`, loops over short
writes, changes it to mode 0444, fsyncs the file, publishes a `.sealed` hardlink
without replacement, then fsyncs the directory. Partials remain as evidence.
For each sequential slot: publish intent first, then bounded raw frame, then
hash-chain commit marker. The frame contains unchanged header/body bytes
(prefixes only on overflow), their SHA-256, exact lengths, status, completeness
flags, slot and intent digest. A valid cutoff's routing marker binds the raw
cutoff frame digest to the derived request policy before slot 1 can start.
The framed metadata is canonical JSON; the raw bytes are never JSON-reserialized.
No request/receipt time is invented: these simulated frames do not provide
real source acquisition timestamps or network provenance.

Any persistence exception poisons that writer. Failure may make even a
negative ledger impossible to persist; no durable success is claimed then.
An independent reader can still return 60 negative outcomes in memory. A
visible commit after failed directory fsync is not proof the original fsync
acknowledged success. Actual power-loss durability, filesystem support and
host enforcement require separate integration evidence; process exit tests
exercise local process interruption, not a physical power cut.

Finalization publishes a create-once ledger with all 85 slot states and all
60 city-days/12 clusters. It preserves five October 4 capture failures, 59
depth abstentions, 14 settlement gaps, the Miami conflict and references to
the 19 previous HTTP 429 receipts. Copied historical metadata status fields
do not confer new admissibility. Every new row is failed-capture or
unverified-offline-only; every source/live/scoring authority flag is false.
An HTTP 200 means only `HTTP_200_UNVERIFIED`; market bodies are not promoted
by this boundary. V1 schema/ladder/rules checks remain separate and unchanged.

## Read-only recovery and integrity

Recovery bounds the file inventory, reads regular files with no-follow,
checks read stability, rejects orphan partials, gaps, out-of-order slots,
unknown files, missing markers, orphan routing and altered bytes, and verifies
claim, intent identity, raw digests, chain, routing and full ledger together.
It never deletes, fills, repairs, resumes or fetches anything. All outputs,
including current-byte integrity matches, are negative research-only outputs.
Missing ledger remains explicitly incomplete. A previously failed write can
never be made into acquisition authority by recovery.

Mode 0444 is accidental-write resistance, not immutable third-party custody.
A local owner can change permissions and rebuild a self-consistent journal.
Hash checks establish internal byte consistency, not provider authorship or
historical acquisition. This is not a defense against a privileged concurrent
filesystem attacker, dishonest caller, patched module or modified roster.
External attestation and trustworthy first-acquisition chronology remain open.

## Absolute deadline design and limits

`AbsoluteDeadlineModel` uses supplied integer monotonic nanoseconds and aware
wall timestamps converted to UTC. DNS → TLS → headers → body share one
absolute request deadline capped by the original total deadline. Phase changes
never renew either deadline. Equality is expiration. Cancellation, invalid
sample/cancellation types, UTC conversion overflow, every invalid transition
and monotonic/wall rollback terminate the model. Request count,
single activity, phase order and start pacing are bounded.

**Supplied-clock simulations do not interrupt a real blocked DNS resolver,
TLS handshake, socket read or filesystem call.** No real clock is sampled or
attested. A future adapter must use an independently killable worker, a parent
monotonic watchdog covering resolver/TLS/header/body/cancellation, bounded
streaming buffers and backpressure, and bounded cleanup/reaping. It must bind
the checked request, durable intent and actual receipt to the same slot and
absolute deadline. A timeout/cancelled attempt consumes its slot and ends
acquisition, never starts a retry or fallback. Real deadline enforcement,
clock freshness/uncertainty, worker escape paths and cancellation latency
must be independently demonstrated on the actual host before live review.

`bound_frame` validates supplied field-block bytes, limits retained headers
to 16 KiB and body to 64 KiB, rejects redirects/403/429, incomplete bodies,
malformed/duplicate headers, non-JSON content type, compression, transfer
encoding and inconsistent Content-Length. The input is already in memory:
this is not proof of bounded live buffering. It is not an HTTP wire parser.
Actual status-line/framing/header parsing and streaming caps remain blocked
integration work. Server Location and Retry-After are never acted on.

## Reproduction and adversarial scope

From this exact checkout, with existing dependencies (no installation needed):

```sh
ruff check services/forecasting/wn_settlement_safety_v2.py services/forecasting/wn_settlement_journal_v2.py tests/test_wn_settlement_boundary_v2.py
mypy --strict services/forecasting/wn_settlement_safety_v2.py services/forecasting/wn_settlement_journal_v2.py
PYTHONDONTWRITEBYTECODE=1 /usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' /Users/ksyme/miniforge3/envs/kalsh3/bin/python -m pytest -p no:cacheprovider tests/test_wn_settlement_boundary_v2.py tests/test_wn_october_settlement_recovery.py -q
```

The last command uses the inspected Mac's existing Python and OS network-deny
profile, inherited by child interruption/race probes. Tests also deny Python
DNS/connect operations. CI's ordinary test suite uses Python denial for these
tests; CI alone is not OS-wide network-isolation evidence. Dependency/CI
infrastructure traffic is distinct from source acquisition.

Synthetic tests cover forged origins/paths/headers/tickers, swapped slots and
alternate routing; every deadline phase and cancellation; cap/pacing/rollback;
429/403/404 and hostile redirects; overflow, truncation and malformed framing;
short/zero writes, disk-full, file/directory/parent fsync and link failures;
every journal publication boundary, repeated invocation, two-process claim
race, actual abrupt process exits, missing markers, corruption and symlinks.
No historical response, score, probability, profitability or trade is produced.

## Authorization boundary and unresolved research conditions

Passing this gate supports offline engineering review only. It cannot confer
source custody, scientific scoring, a probability model, paper profitability,
production authority or trading authority. October 3 remains retrospective
only; October 4 retains failed captures; October 5–14 scoring chronology is
conditional on every other distinct requirement, not a scientific approval.
The Miami provenance conflict, settlement gaps and prior incomplete D1
evidence are not resolved by this code. No October scientific promotion follows.
Live acquisition needs separate explicit approval after all relevant adapter,
host-enforcement, custody and engineering reviews. No merge is performed here.
