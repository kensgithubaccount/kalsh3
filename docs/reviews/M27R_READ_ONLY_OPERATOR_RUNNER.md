# M27R — Read-Only First-Canary Operator Runner

**Status:** IN PROGRESS

## Objective

Add one narrow operator entry point that can assemble the already-reviewed live evidence required by M27Q, invoke `build_first_canary_preflight`, and emit a non-secret review artifact — while making every exchange mutation path unreachable.

M27R must not change candidate selection, weather probability, market economics, fee, settlement, risk, preflight, approval, authorization, or execution policy. It is wiring only.

## Required capability boundary

The runner may perform only the reads required to construct current M27Q inputs:

- public Kalshi GETs already reviewed by M27E/M27J/current market-economics paths;
- current weather/forecast source GETs already reviewed by the M27 weather evidence path;
- authenticated Kalshi GETs already reviewed by M27F and candidate-exposure checks;
- local installed-credential/signer verification already reviewed by M27H, only when required by the preflight evidence contract;
- read-only inspection of the M27P shared first-canary state.

The runner must have no callable path to:

- POST, PUT, PATCH, DELETE, order submission, cancel, amend, or decrease;
- production arming or autonomy enablement;
- M13 authorization issuance or consumption;
- M16 approval creation or consumption;
- M27O release, atomic commit, execution authorization, submission-budget burn, sender, or reconciliation mutation;
- protected write-credential enrollment or write-key access.

## Composition order

A successful run must construct and bind evidence in this order, failing closed at the first unresolved gate:

1. Acquire current public market/event/series/orderbook evidence through existing GET-only helpers.
2. Acquire and verify current authoritative market-rules identity through M27J.
3. Acquire current weather evidence through the reviewed M27 source path and build the reviewed probability/economics candidate inputs without changing policy.
4. Run M27D selection and continue only if exactly one qualifying experimental candidate exists.
5. Only after a candidate exists, perform the exact authenticated M27F account sweep and candidate-specific exposure reads required for that candidate.
6. Verify the installed credential/signer through the existing M27H read-only evidence path if required by the M27I contract.
7. Inspect M27P shared state without mutating it.
8. Call the existing M27Q orchestrator.
9. Serialize only the non-secret M27Q/M27I review artifact and exit.

Authenticated account reads should not happen when no candidate reaches the candidate gate.

## Required output

The runner must produce a deterministic, non-secret artifact containing at minimum:

- run timestamp and software version;
- selected candidate identity, or explicit abstention/block reason;
- source/evidence identities and freshness deadlines needed to audit the run;
- M13 decision/intent/snapshot content hashes, not secret material;
- M27Q state-inspection identity;
- M27I preflight status, gate table, expiry, and content hash;
- explicit `READ_ONLY=true` and `EXECUTION_AUTHORIZED=false` markers.

The artifact must never contain:

- private keys, PEM contents, secrets, tokens, Authorization headers, cookies, or raw credential payloads;
- raw authenticated account rows beyond what the existing reviewed retained evidence schemas permit;
- any reusable authorization or approval object.

## Failure and freshness rules

- Every missing, malformed, stale, mismatched, partial, or ambiguous required input is a BLOCK/ABSTAIN.
- No prior `PREFLIGHT_READY` artifact may satisfy a current run.
- The final expiry is the M27I-derived minimum underlying deadline; M27R must not extend it.
- Network retries, if any are inherited from reviewed GET-only helpers, must never change request method or endpoint class.
- If authenticated pagination is incomplete, the run fails closed.
- If the candidate changes after authenticated reads, the run fails closed and must start a new sweep rather than rebinding old account evidence.

## Test requirements

M27R is not complete until tests prove all of the following:

1. **AST/import capability test:** the operator entry point cannot import production sender/transport, M27O live execution, protected write enrollment, or any mutation helper.
2. **Method allowlist test:** all HTTP paths reachable from the runner are GET-only.
3. **Candidate-before-auth test:** no authenticated account call occurs when selection produces zero or multiple candidates.
4. **Same-sweep test:** the exact persisted M27F artifact equals the exact transient bundle consumed by M27Q.
5. **Pagination fail-closed test:** partial positions/orders/fills/settlements cannot reach M27Q.
6. **Freshness test:** stale weather, market, fee, rules, account, exposure, signer, or shared-state evidence blocks.
7. **No-mutation state test:** the M27P state file is byte-identical before and after every runner outcome.
8. **No-authority test:** a successful run returns `PREFLIGHT_READY` with no M13 authorization, M16 approval, M27O execution authorization, or burn.
9. **Secret-redaction test:** serialized output contains no credential/key/header material.
10. **Deterministic replay test:** identical captured read evidence produces the same non-secret artifact content hash.

## Completion gate

M27R may be called complete only after the runner and the above tests pass the repository test/lint suite. Completion authorizes only a fresh read-only operator preflight. It does not authorize a real-money canary.
