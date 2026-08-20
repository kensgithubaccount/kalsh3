# M27N-W -- Chicago Weather Execution Rehearsal

## Purpose

Prove that one already-valid Chicago weather canary can be deterministically translated
through every pre-send invariant into the EXACT non-secret order request material that the
existing production execution stack would eventually send, while stopping strictly before any
credential/signing/transport/mutation boundary.

This milestone is a **read-only rehearsal**, not an execution path. It never arms production,
never signs, never sends/cancels/amends/decreases an order, never touches a credential loader
or the real signer, and never opens a live SQLite-backed store (`AuthorizationStore`,
`CanaryStore`). Every input it consumes is an OFFLINE FIXTURE -- already-produced evidence
supplied by the caller, never acquired by this module itself.

## Files

- `services/supervised_canary/m27n_weather_rehearsal.py` -- the pure rehearsal library:
  gates, fixture types, `build_rehearsal()`, `validate_rehearsal_artifact()`.
- `scripts/run_m27n_weather_execution_rehearsal.py` -- operator CLI. Builds one deterministic,
  internally consistent, all-pass OFFLINE fixture scenario in-process and prints the resulting
  artifact. No arguments accept external evidence files (there is no JSON evidence-file input
  surface for this milestone -- see "Why no evidence-file CLI input" below).
- `tests/test_m27n_weather_execution_rehearsal.py` -- unit tests against `build_rehearsal()`.
- `tests/test_m27n_weather_execution_rehearsal_cli.py` -- CLI subprocess tests plus
  AST/import-capability safety proofs.

## What is reused vs. what is new

The canonical order/request body is produced **exclusively** by the existing, unmodified
`services.production_execution.requests.create_envelope` (and the
`services.production_execution.domain.ProductionRequestEnvelope`/`build_envelope` it calls).
This module never invents a second order schema.

Candidate selection, thresholding, and ranking are delegated **exclusively** to the existing,
unmodified `services.supervised_canary.m27d.select_experimental_candidate`. The Chicago-only
weather lane (NWS station `KMDW`, GHCND station `USW00014819`, family
`POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z`) is enforced there, not reimplemented here.

Everything else -- M13 risk binding, account-readiness binding, exposure binding, rules-identity
binding, fee-bound checking -- is **independently re-derived** by this module's own gates from
caller-supplied OFFLINE FIXTURE evidence, deliberately without reusing
`services.supervised_canary.m27i` (M27I). See "Why M27I is not imported" below.

## Fixture types (all offline, all caller-supplied)

| Fixture | Represents |
| --- | --- |
| `M13Fixture` | A fresh, already-issued M13 `RiskAuthorization`/`RiskDecision`/`RiskIntent`/`PortfolioRiskSnapshot` quadruple, plus fixture-level halt/compliance/kill-switch clearance booleans. Never consumed (`.consume()` is never called). |
| `AccountSnapshotFixture` | Ordinary M16 account-readiness facts (reads verified, reconciled, write-credential evidence verified, signer-runtime evidence verified) as already-proven booleans -- never re-derived from a live authenticated read. |
| `CandidateExposureFixture` | Secret-free, market-specific proof of no unknown order/position for the candidate's exact ticker. |
| `RulesIdentityFixture` | Already-acquired current-side rules-hash evidence, compared against the candidate's own economics rules hash. |
| `SubmissionBudgetFixture` | Fixture facts about the global one-order canary budget and any unresolved canary session -- never consumed. |

## Gates

`GATE_NAMES` (16 gates, all must pass for `REHEARSAL_READY`):

`single_candidate_bound`, `chicago_lane_bound`, `model_identity_frozen`,
`forecast_evidence_bound`, `candidate_identity_bound`, `rules_identity_current`,
`price_book_current`, `quantity_is_one`, `fee_within_bound`, `account_snapshot_current`,
`no_disqualifying_position`, `no_unresolved_order`, `m13_authorization_fresh`,
`m13_authorization_bound`, `submission_budget_available`, `clock_safe`.

Only when every gate passes AND the computed expiry has not already lapsed does
`build_rehearsal()` call `create_envelope()` to produce the request material. On any gate
failure, `request_body`/`request_method`/`request_path`/etc. all remain `None` -- the artifact
never fabricates a would-be request from an invalid state.

## Output artifact

`WeatherExecutionRehearsal` (`to_json()` is JSON-safe): schema/version, `rehearsal_id`,
`created_at`/`expires_at`, `state` (`REHEARSAL_READY` / `BLOCKED` / `ABSTAIN`), ticker, side,
action, quantity, limit price, fee figures, candidate/model/forecast/economics identities, the
rules hash, the account-snapshot identity, the M13 authorization/decision identities, and --
only when `REHEARSAL_READY` -- the exact `request_method`/`request_path`/`request_origin`/
`request_body`/`request_body_hash`/`request_envelope_content_hash`/`request_execution_id`/
`request_client_order_id` produced by `create_envelope`.

Explicitly excluded, always: signatures, authorization headers, credential material, key IDs,
signer metadata, transport metadata beyond the request's own method/path/origin, approval
tokens, final acknowledgement, submission-counter mutation, and any order ID invented before an
exchange response (`execution_id`/`client_order_id` are internal idempotency identifiers, never
exchange-assigned order IDs).

## Why M27I is not imported

`services.supervised_canary.m27i` is the reviewed, production-facing live preflight, and this
module deliberately does not import it, for two independent reasons:

1. **Transitive network import.** M27I imports
   `services.supervised_canary.m27j` and `services.opportunity_engine.authoritative_economics`,
   both of which reach `services.market_universe.market_snapshot` ->
   `services.market_universe.public_read`, which imports `http.client`/`ssl` -- real network
   transport modules. The AUTHORIZATION for this milestone is explicit: ABSOLUTELY NO live
   network capability, even indirectly reachable and unused. `tests/
   test_m27n_weather_execution_rehearsal_cli.py::test_no_forbidden_module_imports` proves via
   AST scan that neither this module nor its CLI import any network-capable module, directly or
   by name.
2. **Live store construction.** M27I's `build_preflight()` requires a live
   `services.risk_engine.authorization.AuthorizationStore` and a live
   `services.supervised_canary.store.CanaryStore` -- both SQLite-backed. Merely constructing
   either creates/touches a database file (`CREATE TABLE IF NOT EXISTS ...`), which is a
   persistent-state mutation the AUTHORIZATION for this milestone forbids
   ("READ-ONLY INSPECTION ... ABSOLUTELY NO ... production/store mutation").

Because of this, M13 safety-state clearance (global halt / compliance / kill switches) is
accepted here as an OFFLINE FIXTURE fact (`M13Fixture.global_halt_clear` /
`.compliance_clear` / `.kills_clear`), not a live query. A caller who wants that additional
live check should run M27I itself, out of band, before trusting a rehearsal as truthful of the
current moment -- this module's docstring says so explicitly.

## Why no evidence-file CLI input

M27I's rules-identity and account-readiness gates consume small JSON evidence files
(`--m27f-evidence`, `--public-evidence`, etc.) because their upstream producers
(`scripts/m27e_public_read_acceptance.py`, M27F) already serialize to simple JSON dicts. This
milestone's fixtures are richer, hash-chained domain objects
(`PhysicalTemperatureProxyProbability`, `CurrentWeatherForecastEvidence`,
`MarketEconomicsEvidence`, `RiskIntent`, `RiskDecision`, `PortfolioRiskSnapshot`,
`RiskAuthorization`) that have no reviewed, general-purpose JSON round-trip in this repository.
Rather than inventing one (which would itself be a second, parallel serialization surface for
data this repository does not otherwise serialize), the CLI constructs one complete, internally
consistent, deterministic OFFLINE fixture scenario in-process
(`scripts/run_m27n_weather_execution_rehearsal.py::build_scenario`) and rehearses that. The
importable, general-purpose entry point for arbitrary caller-supplied fixtures is
`services.supervised_canary.m27n_weather_rehearsal.build_rehearsal()` itself, used directly (in
Python, not via the CLI) by both test files and by any future operator tooling.

## Safety

Neither `m27n_weather_rehearsal.py` nor `run_m27n_weather_execution_rehearsal.py` imports (even
transitively, at the module level) any of:

- `services.production_execution.{credentials,security_boundary,transport,enrollment,
  enrollment_cli,signer_self_test,installed_credential_verification,store,boundary,
  rate_budget,run}`
- `services.risk_engine.authorization.AuthorizationStore` (only the frozen `RiskAuthorization`/
  `AuthorizationState` types are imported)
- `services.supervised_canary.{m27i,m27j,readiness_report,readiness,candidate_exposure_check,
  store}`
- `services.opportunity_engine.authoritative_economics`
- `services.market_universe.{market_snapshot,public_read}`
- `services.kalshi_account_gateway.client`
- `http`, `http.client`, `ssl`, `socket`, `urllib`, `requests`, `subprocess`

`tests/test_m27n_weather_execution_rehearsal_cli.py` proves this with an AST-based
import/name/token scan (`test_no_forbidden_module_imports`, `test_no_forbidden_names_referenced`,
`test_no_forbidden_tokens_in_source`), confirms neither file executes any top-level side-effecting
call, confirms no production/risk file reverse-imports this milestone
(`test_no_production_reverse_import_of_m27n`), confirms the CLI never writes a SQLite file
anywhere in the repository (`test_cli_never_writes_outside_stdout_stderr`), and confirms `git
diff --stat` is empty for every existing execution/risk/canary/forecasting/market-universe/
opportunity-engine file this milestone was authorized to read but not modify
(`test_frozen_files_have_no_working_tree_changes`).

## Running it

```
conda run -n kalsh3 python scripts/run_m27n_weather_execution_rehearsal.py
conda run -n kalsh3 python scripts/run_m27n_weather_execution_rehearsal.py --now 2026-08-20T12:00:00+00:00
```

Exit code `0` means `REHEARSAL_READY`; exit code `2` means `BLOCKED` or `ABSTAIN`. Nothing this
script does can arm, sign, send, or mutate anything -- it only prints.
