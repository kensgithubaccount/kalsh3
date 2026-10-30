# WN-P1-U1 — Proposed November 1 unattended one-shot amendment (research only)

**Status: PROPOSAL / NOT APPROVED / NOT ARMED.** **Scheduler-contract correction following the conflicting exact-HEAD review of the already merged proposal:** Cloud Scheduler recurrence is NOT an eligible implementation. No cloud task has been created. This is an **amendment request**, not a replacement for the merged manual-only WN-P1 canary. Nothing in this PR schedules a task, accesses Earth Engine, changes the existing collector, grants an unattended-source license, deploys a runner, stores credentials, or authorizes a trade. The inherited manual canary and its frozen source/time/custody contracts remain authoritative until **both** an independent amendment review **and** a separately reviewed runnable execution path receive explicit approval. Parent #240; related #238 and #234.

## Why and precise scope

The operator cannot attend the approved Sunday 2026-11-01 manual source-metadata observation. Propose changing *only the invocation method* to one reviewed, single-host, single-shot unattended execution. Do not broaden the source scope, target calendar day, queries, data bands, city denominator, retry allowance, retention/attestation claims, or downstream permissions of `docs/reviews/WN_P1_PROSPECTIVE_EE_METADATA_ONE_SHOT_V1.md` and `scripts/wn_p1_prospective_metadata_canary.py`.

**Exact proposed event:** target UTC date **2026-11-01**, run-at **2026-11-01T08:05:00Z** (03:05:00 **EST** in New York after the daylight-saving fallback). The canary may start only in the existing `[08:00:00Z, 09:00:00Z)` window and must finish **strictly before 09:00:00Z**. This amendment proposes a **stricter unattended launch tolerance**: if the worker begins before 08:05:00Z, or at/after **08:10:00Z**, **skip without an EE call** and record the scheduler event separately if possible. At **08:55:00Z**, a *separately independently reviewed* OS-level monotonic watchdog must terminate any still-running canary process; that may leave a permanently claimed date without a receipt. The canary's own strict `09:00Z` validation is still required. A successful scheduler exit or process exit is **not** proof of source validity. Do not assume the Earth Engine SDK honors a three-second network timeout or prevents its own internal retries.

**Only one logical EE `getInfo()`** via the inherited fixed asset, 00Z initialization, forecast hours 1..31, `limit(32)`, temperature marginal band names, and five fixed-standard-time development windows (Boston/Miami 5–28, Denver 7–30, Los Angeles/Seattle 8–31). No forecast pixels, `reduceRegion`, raw 64-member GCS, Kalshi book, market signal, data-model training, paper trade, trade, or production influence.

## Scheduler and host design: approval required BEFORE implementation/arming

1. **Pin the execution environment and identity.** Select and verify an always-available execution target with reliable UTC and monotonic clocks, internet access and permitted Earth Engine project. For Cloud Run, independently verify task invocation identity, execution deadline, concurrency, credentials, and a single future Cloud Tasks dispatch; for a noncloud host verify exact OS and native one-shot timer semantics, sleep/reboot and catch-up behavior. **No November execution host or trigger has been selected or independently verified.** Do not substitute a ChatGPT reminder, recurring Cloud Scheduler job, or GitHub Actions cron for a single-occurrence EE runner.
2. **Freeze identity and context.** Pin the reviewed main commit/tree containing both the manual canary and the proposed runner; approved absolute no-symlink receipt-root, explicit Earth Engine project, unprivileged user/working directory, Python interpreter and lockfile/dependencies. Use a read-only Google Earth Engine identity scoped to a documented project; confirm rights, quotas, expected charges, billing responsibility and any applicable provider restrictions. Never log environment variables, tokens, private keys, or service-account credentials. Do not treat user assertion or a local file as external source attestation.
3. **Prepare local custody *before* the UTC window.** Receipt root must already exist and satisfy the inherited FD-bound, `O_DIRECTORY|O_NOFOLLOW` ancestor and create-only claim checks. An unattended wrapper must create and fsync a **separate, persistent create-only scheduler-claim** before invoking the canary, in addition to the inherited `.<date>.claimed` and per-date slot, to block accidental re-launch even if a failure happens before the canary reserves its own date. The marker can be local-only, not tamper-proof. On `EEXIST`, errors, interrupted fsync, or ambiguous marker durability: **no source request, no retry**. Reconciliation may inspect only; it must never delete markers and rerun.
4. **Use exactly one scheduled OCCURRENCE, not cron.** For the proposed GCP path, a separately approved **single Google Cloud Tasks task** with immutable identity and `scheduleTime=2026-11-01T08:05:00Z` is the candidate, **not Cloud Scheduler**. Before task creation, independently configure and verify its dedicated queue's retry policy with `maxAttempts=1`, no application-level retry, no fallback queue, no recurring producer, and no Cloud Run task retry. **Cloud Tasks still permits rare duplicate dispatch even with a one-attempt policy**: the externally durable create-only date claim, acquired **before** the single EE source call, must reject the second delivery. Do not conflate one task object with exactly-once delivery. Pin the UTC instant rather than an ambiguous New York fallback time. An independently verified one-time native timer on a persistent host is an alternative only after a separate approved design. **No task or timer is installed by this PR.**
5. **Fail closed on clock and deadline.** Check timezone-aware wall UTC and monotonic clock *before the scheduler claim and source invocation*. Refuse execution outside the stricter launch window, even if the scheduler fires late; make no EE request. A separately reviewed watchdog must bound process lifetime to before 08:55:00Z without creating a second execution. It must kill and reap the worker once on timeout; an interrupted child may leave poison/claim artifacts and no receipt. Clock rollback, impossible timestamps, supervisor failure or uncertain watchdog semantics => **skip / no positive result**. No late backfill.
6. **Invoke the original canary unchanged.** Use the inherited `--execute-readonly` only from the separately approved unattended runner, targeting `2026-11-01` with the single frozen EE project and receipt root. Keep its own local 08:00–09:00Z checks, raw structured metadata vs source transport hash distinction, five-city completeness/abstention logic, and exit semantics. Do not interpret child exit 0 as authorization of a forecast/probability; inspect its `METADATA_OBSERVED_IN_WINDOW_NOT_ATTESTED` receipt if present.
7. **Preserve negative outcomes.** Record scheduler attempt and final process exit/timeout in a separate create-only, redacted local receipt if safely possible; *do not* misrepresent an absent final canary receipt as source absence. Distinguish `SKIPPED_TOO_EARLY`, `SKIPPED_TOO_LATE`, `RUNNER_NOT_ARMED`, `CLAIM_EXISTS`, `FAILED_BEFORE_SOURCE`, `SOURCE_FAILED_OR_ABSTAINED`, `WATCHDOG_TERMINATED`, `RECEIPT_PRESENT_UNATTESTED`, and `RECEIPT_MISSING_POISONED`. The canary's own marker has precedence. Do not leak stderr secrets or publish provider metadata publicly. Process kill, power loss and writable-disk attacker remain outside strong provenance guarantees.
8. **Single-occurrence task lifecycle and expiry, including a missed attempt.** Cloud Tasks is not a recurring registration: a single task is consumed on completed delivery or stopped after its independently verified one-attempt retry policy. **If it is never dispatched, the application-level authority expires absolutely at `2026-11-01T08:10:00Z` even though the task object may remain queued**: any late delivery must exit before the GCS claim and EE call. Pending tasks are eventually deleted by the provider's configured queue task-retention TTL (which can outlive the UTC gate and must be checked during host review), or an explicitly authorized operator may later delete the stale task **without running it**. Do not claim Cloud Tasks automatically deletes an undelivered task at 08:10Z or that administrative cleanup occurred. No recurring job or scheduled next occurrence may exist. After a first attempt or miss, review read-only evidence and leave persistent claim/receipt artifacts intact; never re-arm, recreate, retry or backfill November 1. A new target requires a new independently frozen experiment.
9. **Separate deployment authorization.** No scheduler can be installed merely because this amendment doc is merged. Require an independently reviewed executable wrapper and injected **fully offline synthetic tests** plus environment-specific arm/rollback audit, then an explicit user go/no-go after evidence of host/rights/cost. Only then may an operator *deliberately* arm one job. **This PR alone is not the requested unattended capture implementation.**


## Recovered candidate Google Cloud host — NOT live-verified

GitHub's frozen October C1 final report and `final_results.json` identify the *historic* research deployment:

- GCP project `total-market-138523`, region `us-central1`.
- Cloud Run **October collector** `wn-multicity-20261003-20261014-collector`, five tasks, five-way parallel, max task retries zero, October-specific container image `sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08`.
- An **earlier Chicago October job** is also identified in the frozen design: Cloud Run `wn-ee-20261001-20261014-collector` and Cloud Scheduler `wn-ee-20261001-20261014`. The user's reference to the previous failed test may refer to this older job rather than the later five-city deployment. Both are **historical identities only**; do not infer current running/paused state or reuse either frozen collector.
- Cloud Scheduler **October trigger** `wn-multicity-20261003-20261014`, UTC schedule `50 8 3-14 10 *`, previously recorded **PAUSED**, retry count zero. That record is dated 2026-10-01 and **cannot prove today's actual scheduler state**, EE credentials, current authorization or prior attempts.
- Historical GCS evidence bucket `wn-multicity-c1-354269857197-20261001` had successfully tested conditional create-only writes. This is **not** advance authorization to store November metadata, read or reuse October artifacts, or expand historic service-account IAM.
- The old container samples four station-head temperature bands, uses Kalshi public book endpoints, and enforces an **October 09:00Z** gate. It is NOT the November **metadata-only** source and must **never be resumed, rerun, repurposed in place, or have its frozen image/config edited** to achieve the new experiment.

### Cloud Run filesystem mismatch — execution BLOCKER

The merged November 1 canary uses POSIX `O_DIRECTORY|O_NOFOLLOW`, `O_EXCL` local claim marker, per-day directory, and `fsync` to enforce a cooperative **persistent-local-root** one-shot guarantee. An ordinary Cloud Run job's writable container filesystem is **ephemeral and scoped to each instance**: a second execution can start with a fresh empty directory, so calling the unchanged local canary *inside a Cloud Run container alone* would NOT provide durable exactly-once custody. A GCS FUSE mount cannot be assumed to implement the POSIX create-only/fsync guarantees either. A plain Cloud Scheduler trigger is also not an exactly-once execution guarantee.

Before selecting Cloud Run, a **separate reviewed wrapper/transport boundary** must:
1. Establish an independently permissioned durable *external* GCS **create-only one-date scheduler claim** with provider-enforced `if_generation_match=0` (no delete/overwrite on the runtime identity) **before any EE/source call**, and abort on precondition failure or ambiguous network error **without retry**. A second run must encounter the existing claim even if the first job/container died without output.
2. Retain the original canary's local date-claim/receipt logic where applicable, but treat any local receipt as **ephemeral** until safely copied/committed into a separately reviewed create-only GCS evidence object, with explicit receipt byte SHA256, generation and failure custody. Never claim this is the original Earth Engine raw transport or strong provider timestamp attestation.
3. Use a **new, isolated one-task Cloud Run Job**, not the five-task October collector. Require reviewed image/source SHA, frozen args/EE metadata asset/project, no Kalshi/GCS-member access, zero Cloud Run task retries, no concurrent invocations, no service/job auto-restart, process-level deadline/watchdog and exact UTC gate checks. A second source call after ambiguous process kill is forbidden.
4. **Do not create, resume or repurpose a Cloud Scheduler cron registration.** Use at most **one separately approved Cloud Tasks task** scheduled for `2026-11-01T08:05:00Z`, in a dedicated queue with independently reviewed `maxAttempts=1` and an authenticated, narrowly scoped one-time Cloud Run invocation path. Validate the task's `scheduleTime`, task ID, effective **queue-level** retry policy, service identity and read-only deployment configuration before arming; task creation itself is an explicit separate permissioned action. Because even one task can deliver twice or late, the worker MUST check exact UTC date and `[08:05Z,08:10Z)` **before making its external GCS claim or any source call**; the GCS create-only claim then prevents a second request. A task never dispatched by 08:10Z loses all scientific/source authority immediately; stale task objects may persist until the provider's queue TTL or authorized cleanup and cannot regain authority. No task or cloud scheduler has been created by this document.
5. Verify an approved, narrow read-only Earth Engine credential is actually available and valid in the **new** runtime, along with explicitly budgeted requests and a new private receipt destination. Do not copy prior October refresh tokens or assume the old Secret Manager mount is authorized for the new canary. Do not print credentials, token bytes or env secrets.

### Owner/operator **read-only** host verification (no EE request and no scheduler changes)

These commands are inspection only, not activation. Execute with an already authenticated local `gcloud` CLI; **do not share credentials, tokens, secret values or full environment dumps**. Historical names are *candidates*, not live-confirmed resources:

```bash
PROJECT=total-market-138523
REGION=us-central1
gcloud config get-value project
gcloud run jobs describe wn-multicity-20261003-20261014-collector \
  --project="$PROJECT" --region="$REGION" \
  --format='yaml(name,template.template.taskCount,template.template.maxRetries)'
gcloud scheduler jobs describe wn-multicity-20261003-20261014 \
  --project="$PROJECT" --location="$REGION" \
  --format='yaml(name,state,schedule,timeZone,retryConfig)'
gcloud run jobs describe wn-ee-20261001-20261014-collector \
  --project="$PROJECT" --region="$REGION" \
  --format='yaml(name,template.template.taskCount,template.template.maxRetries)'
gcloud scheduler jobs describe wn-ee-20261001-20261014 \
  --project="$PROJECT" --location="$REGION" \
  --format='yaml(name,state,schedule,timeZone,retryConfig)'
gcloud run jobs executions list \
  --project="$PROJECT" --region="$REGION" \
  --job=wn-multicity-20261003-20261014-collector --limit=5 \
  --format='table(name,createTime,completionTime,conditions)'
```

Do **not** run `gcloud scheduler jobs resume`, `gcloud run jobs execute`, `gcloud scheduler jobs run`, deploy, IAM/secret mutation, or any source GET as part of this inspection. If these queries fail (permission denied or resource absent), report only the redacted error type and **stop**. The availability of a Google Cloud project in archived GitHub files does not confer our present ability to inspect/manage it from ChatGPT or certify it as safe.

**Host go/no-go:** A credible existing Google Cloud *candidate* was recovered; no November-ready, durable exactly-once Cloud Run runner or independent current operational validation exists. Therefore **UNATTENDED NOVEMBER 1 STILL NOT ARMED**.


## Scheduler-contract repair rationale (supersedes the merged proposal's incompatible paragraph)

Cloud Scheduler is a **recurring** cron/at-least-once dispatcher and cannot simultaneously satisfy the prior hard requirement for a genuinely one-shot registration. It is **explicitly rejected** for the November 1 unattended trigger. Google's Cloud Scheduler documentation itself recommends Cloud Tasks for a one-off execution. Cloud Tasks supports a single RFC3339 `scheduleTime` task, but its queue can dispatch duplicates and a task that has never dispatched can remain stored beyond the intended launch time. **No scheduled one-off Google Cloud service is treated as a perfect exactly-once issuer.**

Two independent expiry mechanisms are required:

- **Scientific/source authorization expires permanently at 08:10:00Z**, whether the single task ran, failed, was delivered twice, was delayed, or was never delivered. Late attempts must fail at the worker's first date/time gate, with no scheduler claim or EE request. A missing first attempt does **not** extend the window.
- **Task object lifecycle:** A successfully dispatched task is completed/retired under the approved one-attempt retry configuration. An undelivered task may remain in the provider queue until its configured task-retention TTL or authorized no-dispatch cleanup. A provider task retention TTL is **not** assumed to equal the scientific cutoff; verify it and document the expected stale-task lifecycle before arming. No recurrence is present and no automated retries or follow-on tasks may be created. Do not arm at all if this lifecycle cannot be independently established.

Even a Cloud Tasks dispatch at 08:05Z is only a trigger. A single-task Cloud Run invocation, strong external GCS `if_generation_match=0` source-claim binding, bounded watchdog, absolute decision-time checks, one logical EE `getInfo()`, and private create-only receipt still need **separate runnable implementation, synthetic offline adversarial review and operational approval**. The Earth Engine SDK may perform internal retries; this plan does not falsely claim network-level exactly-once source access.

Provider references:
- https://docs.cloud.google.com/scheduler/docs/creating
- https://docs.cloud.google.com/tasks/docs/create-tasks
- https://docs.cloud.google.com/tasks/docs/configure-retry-task
- https://docs.cloud.google.com/tasks/docs/common-pitfalls
- https://docs.cloud.google.com/tasks/docs/reference/rpc/google.cloud.tasks.v2beta3

**This follow-up corrects policy wording only, after PR #252 was already merged on a prior PASS. It is not a post-hoc edit of the independently reviewed/merged commit and does not deploy anything.**

## Required independent review and evidence

Before changing manual-only policy or arming anything, reviewer must:

- Pin exact HEAD/TREE/main/merge base; check the diff remains *documentation only* and all existing WN-P1 canary/tests, CPI, P11, October and production paths remain frozen. `git diff --check`, `git fsck --strict --full`, clean worktree and all four exact-head CI/security jobs.
- Adversarially challenge the **single Cloud Tasks task vs recurring Cloud Scheduler** distinction, queue-level `maxAttempts=1`, duplicate delivery despite no requested retries, stale undelivered task after 08:10Z, queue task-retention TTL vs immediate *application authorization expiry*, UTC/DST conversion, host failure, concurrent executions and missing credentials. The second or late dispatch must make **zero** source calls, independent of stale queue-object cleanup. **The published time is UTC 08:05Z; 03:05 EST is a display conversion only.**
- Review that any runnable follow-up wrapper must have injected synthetic clocks/transport/subprocess/scheduler and tests for early, exact-on-time, 08:10Z too late, pre-08:55Z kill, 09:00Z hard canary cutoff, duplicate scheduler/canary markers, fsync failures, poisoned claim, child nonzero/kill/crash, unexpected `BaseException`, absent receipt, and no retry under every state.
- Verify the host can remain online, precise source query rights/fees/quotas/project identity, clock integrity, absolute receipt-root safety, stdout/stderr redaction, least privileges, and independently test scheduler installation at a *different dummy time with a NO-SOURCE command* before any authorization. An actual source test in CI/review is forbidden.
- Preserve all negative authority flags and the empty model registry. A successful metadata observation proves neither original provider raw bytes nor 64-member trajectories, TWC settlement semantics, probability, paper readiness, or trading edge.

## Go/no-go decision

**CURRENT VERDICT: NOT ARMED / NOT RUNNABLE.** The amendment can be evaluated and merged as *proposed policy* only after separate independent PASS; a distinct reviewed runner and a one-shot host operator approval are prerequisites to executing. If the host/rights/cost, standalone runner safety, fresh exact-head CI or independent review cannot all be completed **before 2026-11-01T08:05:00Z**, **NO-GO** for unattended November 1. Preserve the missing observation rather than reschedule, retry, or infer source availability.

*Authority freeze:* `scientific_evidence_authorized=false`; `source_attestation=NONE`; `model_probability_authorized=false`; `paper_signal_authorized=false`; `trading_authority=NONE`; `production_influence=0`.
