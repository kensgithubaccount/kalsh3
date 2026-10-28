# CPI P12 — Off-host archival and crash-recovery proof (proposed v1)

**Issue #230; November go/no-go ledger #214, gate #10.**
**Starting canonical base:** `9c09bdcdc423f95ce4ff8ebfa10eff3205a61f32` (2026-10-28).
**Status:** RESEARCH-ONLY **DESIGN PROPOSAL**, NOT IMPLEMENTED, NOT REVIEWED
AS AN OPERATIONAL DEPLOYMENT. **P12 NOT FROZEN / UNKNOWN_NOT_READY**.
No GCS command, network request, November Reuters target read, source selection,
forecast extraction, live scheduler deployment or trade is performed by this
document. Frozen P11 remains unchanged.

## 1. What is historically proved—and what is not

The October 21 BLS manual-browser calendar receipt is already preserved under
`docs/reviews/CPI_P12_BLS_SCHEDULE_RECEIPT_20261021.md` and the future
event-binding limits are described in
`docs/reviews/CPI_P12_BLS_SEALED_RECEIPT_EVENT_BINDING_SPEC_V1.md`.
The **previously reported**, contemporaneously streamed GCS readback matched
these SHA-256 values:

| Previous sealed artifact | Historical SHA-256 |
| --- | --- |
| Original BLS HTML (55,389 bytes) | `cece37b1f964eed1cd51215764a55fb26e310da31b441ab9c65758989c9afc17` |
| Imported BLS receipt | `01fc6542b73b0b6d293d91c024d2ab0be7c5ee8928f11f624eb32ac35e50619d` |

That is a genuine **historical BLS upload/readback checkpoint**, not proof that
the objects have the same bytes today; that the future November runner can
access them; that Kalshi/Reuters artifacts can be uploaded, safely retried and
recovered; or that any source is admissible for scientific testing. The object
paths and bucket previously recorded for the BLS receipt are historical, **not
a newly approved output prefix for future live artifacts**.

Existing `services/forecasting/cpi_p12_decoupled_collection.py` writes
create-once stage receipts with a durable `.pending` veto and revalidates
artifact hashes. Its `READY_ALL_REQUIRED_STAGES_PRESENT` is strictly an
**operational presence** finding and neither attests a remote archive nor
authorizes scientific evidence. The three independent stage transport paths
retain their own scope. A returned source record and even a local PASS never
stand in for remote durability or source identity.

## 2. Proposed future objects and binding boundaries

A future **separately reviewed and explicitly approved** archive protocol
must prospectively seal the following values before the first relevant
observation. **This memo selects none of them**:

1. Run/event protocol digest, exact artifact schemas and stage names, immutable
   source provenance and provenance acquisition times, cutoff/clock budget and
   whether each stage has been independently admitted. Do not derive
   event-specific contents from a guessed ticker or previous P11 values.
2. Authorized GCS project, bucket, unique per-run prefix, access principal,
   storage retention/versioning configuration, generation-precondition support,
   readback route and failure/alert destinations. Separate acquisition from
   verification privileges; prohibit object overwrite/delete where practical.
3. Bounded maximum bytes and permissible raw-data classification/retention
   **per source**. In particular, Reuters retrieval/archival rights remain
   unverified under Issue #215: no design claim grants license to store Reuters
   content. Do not send unlicensed target source bytes to GCS.
4. Each stage's local create-once receipt path and artifact path(s), exact
   SHA-256 + byte counts calculated from **read-back local bytes**, and stage
   evidence identity. Never accept a caller-provided hash without re-reading
   its corresponding artifact. The local `.pending` veto must be absent,
   the receipt must pass existing integrity validation, and failure receipts
   must not masquerade as PASS artifacts.
5. Remote object name, content hash, byte count, immutable GCS **generation**
   (and relevant metageneration/metadata), upload attempt identity, exact
   server response/error, and subsequent independent **generation-pinned raw
   readback**. A URI, metadata, CRC32C, or HTTP 200 response alone is **not**
   equivalent to byte-for-byte SHA-256 proof. Treat CRC32C as an additional
   transport checksum only.

All remote artifacts and archive receipts remain nonauthorizing. The archive
proof is never allowed to change `scientific_evidence_authorized=false`,
`phase1_economic_test_authorized=false`, `p12_frozen=false`,
`production_influence="0"`, `promotion_authority=NONE` or trading NONE
on current P12 source/orchestration records.

## 3. Proposed create-once upload and readback sequence

**Specification only; no current uploader is approved by this document.**

1. Acquire and write each stage's source artifact and local operational receipt
   under its independently reviewed stage contract. A crash or `.pending`
   receipt prevents that stage from being considered locally committed. Do not
   transform a FAIL/MISSING stage into PASS in order to archive it.
2. From a separate, least-privileged archival process, verify the existing
   receipt and local artifact hashes without invoking **any** source GET,
   reacquiring a Reuters article or mutating sibling stages. Record immutable
   source origin, original request/response instants, source hash and cutoff
   claims *as unauthorizing data*, not as newly attested facts.
3. Use a **create-if-absent** conditional upload for each uniquely named
   object (for GCS, where applicable, `ifGenerationMatch=0`). Never use
   ordinary overwrite or a replace-on-retry fallback. If an object exists,
   first verify **its exact expected bytes and generation** under a reviewed
   recovery policy; an existing name is not evidence of prior success.
4. Pin the server-returned generation. Independently fetch and stream that
   **specific immutable generation** through the authorized reader; compute
   SHA-256 and length locally, compare against the original local re-read,
   and validate recorded metadata identities. A generation change or inability
   to read exact bytes fails closed even when upload returned success.
5. Only **after** all required artifacts and stage receipts have been
   generation-pinned, completely read back, and digest-matched may an
   **archive-only confirmation receipt** be durably committed with the same
   create-once/`.pending` safety principles. It must explicitly identify
   whether each required stage was PASS, FAILURE or MISSING, and must never
   rewrite the original stage receipt or preclose gate.
6. An archive-only confirmation can assert `remote_bytes_verified=true`
   **solely for its exact listed object generations and bytes**. It cannot
   assert a source's scientific provenance, legal rights, completeness,
   publication time, first-party attribution, event close, execution capacity,
   admissibility or a P12 scientific PASS.
7. Persist a separate audit transcript of attempted steps and sanitized
   failures. Raw URLs with secrets, credentials, internal auth responses or
   unlicensed source content must not enter logs, error receipts or PRs.
   Incomplete upload or failed confirmation remains INCOMPLETE even if some
   earlier objects happen to exist.

No workflow may perform retrospective source selection, post-cutoff recovery
of missing *source evidence*, or replace original stage evidence with a later
favorable article/price. Recovery of the **same independently preserved
pre-cutoff bytes** is a separate storage question, not permission to obtain
those bytes after cutoff.

## 4. Required safety, provenance and recovery invariants

- **No silent gap:** A locally complete PASS plus missing/incomplete remote
  receipt cannot be interpreted as off-host proof. Local PASS and remote
  storage success are different propositions.
- **No sibling cascade:** BLS, Kalshi and Reuters outputs retain disjoint
  namespaces and independent process failure domains. A failed archive of one
  stage must not alter other stage receipts or suppress their attempts.
  Overall archival completeness is still false when a required stage is absent.
- **No replay promotion:** An identical retry may verify an existing exact
  generation with exactly matching bytes, but must never choose a new
  generation or replace contradictory bytes merely because retry succeeds.
  Any mismatch is a conflict requiring explicit review.
- **No stale/cached proof:** A remote `HEAD`, metadata lookup, cached proxy
  read, object listing, or unsigned filename cannot replace an independent,
  generation-specific full readback. A verifier must record where/when the
  read happened and the exact generation used.
- **Crash and fsync defenses:** A visible full JSON body with an uncommitted
  `.pending` veto is NOT a committed receipt. Failed final fsync, guard
  unlink, directory sync, rollback cleanup, archive receipt write or poison
  rewrite must never result in verified/operational/scientific PASS.
- **No path escape:** Reject traversal, symlink escapes, duplicate artifact
  paths, stage namespace aliasing, unexpected absolute paths and reused
  object IDs that collide across runs or stages.
- **No forged source independence:** Different object paths or buckets do
  not make syndicated Reuters copies independently authored, and storing an
  article cannot prove original public release chronology.
- **Retention constraints are binding:** No repository code or rehearsal
  may infer permission to persist copyrighted third-party bytes, especially
  Reuters Connect material, from an operational write capability.
- **Authority remains separate:** Storage correctness is required but not
  sufficient. A complete remote manifest cannot convert caller-selected
  markets, unknown Kalshi rules, Reuters timing or missing witnesses into
  scientific evidence.

## 5. Proposed independent adversarial rehearsal matrix

These are **planned** tests, not completed tests. Use invented bytes in
synthetic storage doubles, and only later a separately approved isolated
off-host storage prefix with synthetic sentinels. Do not include November
Reuters source contents, actual Kalshi market prices, credentials or trading.

| ID | Adversarial condition | Required outcome |
| --- | --- | --- |
| A01 | Local receipt PASS and exact local hashes, but no remote object | ARCHIVE INCOMPLETE |
| A02 | `.pending` exists beside otherwise complete local PASS JSON | Reject before upload/confirmation |
| A03 | Failed local receipt final fsync or directory fsync | Never claim committed local/remote PASS |
| A04 | Upload stream truncated or only first object exists | INCOMPLETE; no manifest PASS |
| A05 | Upload returned success, remote bytes changed by one byte at same length | Independent SHA-256 mismatch; FAIL |
| A06 | Existing same-key object contains different bytes, generation or source identity | Conflict; no overwrite or best-effort reconciliation |
| A07 | Conditional upload races another writer | One immutable winner only; loser verifies exact bytes or fails |
| A08 | HTTP/object metadata reports success but full readback fails/403/timeout | INCOMPLETE, never infer success |
| A09 | Object generation differs between initial response and readback | Reject stale/generation-substituted proof |
| A10 | Verifier uses listing/metadata/CRC32C only, not complete SHA-256 readback | Reject as missing independent byte proof |
| A11 | Archive receipt write, guard unlink or directory sync fails | No archive-confirmed PASS, even if remote bytes exist |
| A12 | Restart after crash with existing matching objects | Verify original exact generations; no refetch or overwrite |
| A13 | Restart with conflicting existing object, poisoned guard or manifest | Refuse to promote/retry-by-replacement |
| A14 | One stage missing/FAILURE while siblings have correct remote objects | Preserve siblings; overall not complete |
| A15 | Corrupt local file/receipt after capture but before upload | Reject; do not allow remote source hash substitution |
| A16 | Role lacks write, read, generation-read or permission is revoked mid-run | Fail closed, no fabricated confirmation |
| A17 | Wrong bucket/project/prefix or path traversal / symlink escape | Reject without touching foreign object |
| A18 | Two hostnames / storage objects represent one Reuters syndicated story | No independent source/corroboration claim |
| A19 | Post-cutoff source reacquisition offered to fill a missing source | Reject as scientific backfill, even if archivable |
| A20 | Everything archives and matches but source protocol/contract authority unknown | Archive proof only; P12 still NO SCIENTIFIC TEST |

A positive **synthetic** A00, where all bytes match after exact-generation
readback, proves only that the rehearsal's storage contract works for the
invented fixture. A future live-environment rehearsal must be separately
reviewed and approved and still cannot overcome source-authority gaps.

## 6. Evidence required before operational gate #10 could close

An independent reviewer should verify and preserve:

- Exact runner code HEAD/TREE, immutable protocol identity and explicit
  pre-authorized isolated storage destination; approved credentials and
  least-privilege role assignment.
- Clean-room synthetic fault-injection commands and logs for A01–A20, covering
  first-run and retry, no data substitution, no uncommitted PASS and all stage
  siblings.
- Actual intended-environment *synthetic-only* conditional writes and
  generation-pinned full-byte readbacks, including hash, length, server
  generation, attempt time, runner identity and role/permissions evidence.
- Durable create-once local and remote confirmation receipt validation,
  independent retry/reboot checks and separately monitored failure alarms.
- Independent CI + exact-head adversarial review with explicit PASS/REPAIR
  REQUIRED/REVIEW BLOCKED for **only** the defined operational claims.

Until this separately authorized proof exists, gate #10 is **UNPROVEN**.
An operationally complete archive would still leave Kalshi #213
complete-cohort/rule-version authority and Reuters #215 route, rights,
publication chronology and independence unresolved. **No scientific event
freeze is authorized by this specification.**

## 7. Stop rule and timing

This review checkpoint is deliberately bounded to documentation. Planning:
Oct 29–Nov 3 is the proposed **synthetic rehearsal window** only after
approval and implementation; Nov 4–6 is a *conditional* scientific freeze
decision only if all independent source and operational gates are complete.
The event's historical schedule receipt lists October 2026 CPI for
2026-11-10T13:30:00Z, but neither this timestamp nor a passing synthetic
archive can authorize a November predictor/market observation.

If original source, allowed retention, independent publication chronology,
contract rules, as-of sibling roster, clock/cutoff proof, off-host evidence or
scientific reconciliation remain unknown, record **NOT_READY /
NO_SCIENTIFIC_TEST / INCONCLUSIVE** without post-cutoff backfill.

Permanent boundary: research-only, scientific/economic test authorization
false, `p12_frozen=false`, `production_influence="0"`,
`promotion_authority=NONE`, trading NONE.
