# M24 — Perps Shadow Research Layer

## Scope and acceptance

M24 is a research-only milestone for immutable perps market, portfolio-margin, quote, and
edge-decay observations. It has zero production influence and enables no perps trading. It adds
no execution, order placement, cancel/amend, sizing, routing, credential, networking, or API-write
capability. `exchange_index` is first-class, long and short leverage estimates remain separate,
and absent portfolio-margin fields remain nullable and uninferred.

## Review findings and corrections

Codex review found and corrected fail-closed contract gaps: stored edge and latency values are
validated against their immutable source values and timestamps; `value_at_creation` is retained;
timestamps must be timezone-aware, monotonic, and exactly millisecond-representable; invalid
directions cannot fall through to SHORT; raw payload mappings and containers are recursively
copied and frozen while unsupported custom values fail closed; and `exchange_index` and
`subaccount` now require exact non-negative integers, explicitly rejecting booleans.

An independent Claude review was completed. Its findings were incorporated before acceptance,
including the research-only boundary, immutable/auditable edge-decay records, raw-payload
immutability, identifier validation, nullable uninferred portfolio-margin fields, and separate
directional leverage.

## Verification

- Focused test suite: **12/12 passed**.
- Ruff lint: **passed**.
- Ruff format check: **passed**.
- Full-repository mypy remains blocked locally only by the pre-existing macOS `memfd_create`
  typing/platform issue in `services/production_execution/security_boundary.py`; M24 does not
  modify that file or issue.

Focused deterministic coverage includes LONG and SHORT edge math, invalid direction, naive and
out-of-order timestamps (including the direct measurement entry point), sub-millisecond latency,
contradictory stored edges/latencies, immutable raw snapshots and unsupported values, exact-int
`exchange_index`/`subaccount` validation, nullable margin fields, `QuoteObservation`, and zero
production influence across every shadow observation type.

## Safety confirmation

The research-only boundary remains intact. No production execution file changed. No execution,
trading, order placement, sizing, routing, cancel/amend, credential, signer, API-write, risk-engine,
bounded-autonomy, or supervised-canary behavior was introduced or modified.
