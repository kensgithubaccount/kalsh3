# FR-A3R.3A.1 replay authority boundary

This checkpoint keeps FR-A3 explicitly research-only and fixture-only. It does
not establish a genuine production outcome source, a prospective cohort, PnL,
execution, capital, or production influence.

## Authorities and trust chain

- FR-A1 `ProspectiveReceiptStore` authenticates and freezes the forecast receipt
  and its publication record.
- FR-A2 `TrialLedger` authenticates the trial definition and status history.
- The reviewed fixture `OutcomeEvidenceAuthority` authenticates outcome receipts,
  freezes their artifact bytes under a content address, and reparses those bytes
  on every validation. Its capability is private to the module and is not
  exported as a production adapter.
- A non-empty `OutcomeScoringStore` can be opened only with an explicit
  `ReplayContext` containing all three authorities. Replay authenticates the
  journal, checkpoint, outcome receipt, artifact, forecast publication, trial
  history, identities, and deterministic scores.

The scoring journal MAC is an integrity layer, not a replacement for the
registered authorities. Secret keys are kept in authority-side files and are
never embedded in scoring records.

The only reviewed append call site is `score_trial()` in
`outcome_scoring.py`; the public `append()` method is intentionally rejected.
Aggregation and calibration are pure diagnostic functions and do not establish
authority for caller-constructed records; authoritative callers must supply
records obtained from replay with a complete `ReplayContext`.

## Lifecycle and chronology

Store creation is explicit and create-only. Opening requires the key, journal,
and checkpoint; missing genesis material fails closed. Appends are serialized,
duplicate-trial checked, journal-fsynced, and followed by an atomic checkpoint
update. The unavoidable limitation is that deletion of every store artifact,
key, and external commitment cannot be detected by a local store alone.

Scoring requires:

`trial.created_at <= receipt.decision_at <= authenticated forecast publication < outcome.published_at <= outcome.available_at <= outcome.acquired_at <= issuer-owned scored_at`

The strict forecast-publication boundary closes look-ahead. Caller clocks do not
provide `scored_at`.

## Semantics and aggregation

Resolved, non-abstained forecasts receive binary Brier and clipped log-loss
scores under the fixed policy version. Abstentions and unresolved, cancelled,
invalid, no-release, and terminal cases remain unscored with explicit status.
Event aggregation averages sibling markets within an underlying event before
event-equal aggregation. Calibration output includes trial count, event count,
mean forecast, observed rate, and empty buckets.

## Not established

The JSON artifact adapter remains a test fixture and does not prove an
authoritative external source. No cohort has been registered or scored, and no
network, credentials, account mutation, orders, risk authorization, sizing,
capital allocation, PnL, or production authority exists.

## Terminal lifecycle matrix

| FR-A2 trial state | Outcome | Result |
| --- | --- | --- |
| PLANNED/RUNNING | any | rejected without scoring-journal mutation; retryable |
| COMPLETED | RESOLVED + forecast | `SCORED` |
| COMPLETED | RESOLVED + abstention | `ABSTAINED`, no scores |
| COMPLETED | PENDING/UNKNOWN | rejected; no trial identity consumed |
| COMPLETED | NO_RELEASE/CANCELLED/INVALID | `TERMINAL_UNSCORED` |
| FAILED/ABANDONED | any validated terminal evidence | `TERMINAL_UNSCORED` under the explicit research-only policy |

Terminal FR-A2 histories cannot transition, so replay can require the exact
registration-history identity recorded at issuance.

## Test inventory

The Forward Reality-related inventory is the eight files matching
`rg --files tests | rg -i 'forward|fr_a[123]|prospective|trial_ledger'`:

`test_fr_a1_prospective_receipts.py`, `test_fr_a2_trial_ledger.py`,
`test_fr_a3_outcome_scoring.py`, `test_m27c_prospective_blind_weather.py`,
`test_m27l_prospective_capture.py`, `test_m27l_prospective_capture_cli.py`,
`test_m27m_prospective_operations.py`, and
`test_m27m_prospective_operations_cli.py`.
