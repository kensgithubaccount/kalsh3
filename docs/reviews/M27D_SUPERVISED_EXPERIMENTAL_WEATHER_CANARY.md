# M27D — Supervised Experimental Live Weather Canary

## PURPOSE

M27D adds a reviewable bridge for one supervised, real-money, one-contract
operational/strategy experiment. It is shadow-only in this implementation.
The candidate builder has no signer, credential loader, POST, DELETE, arm, or
order capability.

## NOT A CLAIM OF ALPHA

The frozen model claim remains `GHCND_PHYSICAL_TEMPERATURE_PROXY` and the
settlement status remains `UNVALIDATED_GHCND_PROXY`. M27C Part 2C1 established
`NO_AUTHORITATIVE_TWC_VALUE_EVIDENCE`. The numerical comparison is named
`research_probability_discrepancy`; it is not a Kalshi settlement probability,
fair value, edge, expected value, validated alpha, or profitability claim.

## CANDIDATE POLICY

Policy `m27d-august-taker-now-20pp-v1` accepts only `TAKER_NOW`, exactly
`Decimal("1.00")`, and an absolute research discrepancy of at least
`Decimal("0.20")` against the conservative one-contract entry commitment.
YES and NO are evaluated independently; SELL, maker, post-only, repricing,
averaging down, and retries are unsupported. Boundary-mass probabilities and
unknown fee bounds abstain. Multiple candidates rank by discrepancy, shortest
exact horizon, then ticker.

## AUGUST WINDOW

Only local target dates 2026-08-18 through 2026-08-31 are accepted. September
1, 2026 and later are hard rejected so this experiment cannot enter the frozen
M27C Part 2B3 prospective blind period.

## EXECUTION SAFETY

M27D reuses M27A immutable read-only economics, M13 deterministic risk, M16
preview/approval/final-revalidation/reconciliation controls, and the M15
permanently disarmed production boundary. No execution semantics were changed;
the only durable addition is a one-time submission counter in the existing
canary store. The stronger acknowledgement is bound into an experimental
approval hash and generic M16 confirmation is insufficient.

## ONE-ORDER LIMIT

The durable v1 submission counter permits at most one real opening-order
submission globally and survives restart. A possible submission, unknown
order, partial fill, fill, or cancellation after possible submission ends this
version’s availability. The quantity is exactly 1.00.

## LIVE SHADOW RESULT

The no-write operator entry point `scripts/run_m27d_weather_shadow.py` returned
`ABSTAIN` with no evidence bundle. A separate single bounded public GET of the
current open `CLIMDW` market endpoint at 2026-08-17 returned zero markets, so
there were zero eligible markets, zero orderbooks, and zero qualifying
candidates to evaluate. No forecast artifact was altered and no order was
sent. A later bounded collector invocation must record current
Market/Event/Series, exact orderbook, fee, and current NOAA/GRIB evidence
identities before any candidate can be reviewed.

## WRITE READINESS

No production-write credential was installed, requested, inspected, or used.
M15 remains `DISARMED`; the existing reviewed credential is read-only only.
Signer/write readiness and live M16 gates are therefore not satisfied.

## NEXT HUMAN STEP

Independently review this milestone and the preserved M13/M15/M16 gates. Then,
if still approved, a human operator must perform the separately controlled
read-only evidence collection and preview review, establish any required
production-write credential through the approved process, and provide the
strong acknowledgement plus existing password/TOTP/CSRF/owner proof. This
milestone itself authorizes no write.

Real canary completion is not claimed.
