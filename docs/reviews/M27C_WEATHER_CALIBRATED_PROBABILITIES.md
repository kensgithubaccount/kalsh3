# M27C Part 2B2 — Research-Only Physical-Temperature Proxy Probabilities

Status: model mechanics implemented and fixture-tested; empirical performance not yet accepted.

## Implemented model mechanics

The only supported lane is `CLIMDW → KMDW → USW00014819`, `DAILY_MAX`,
`POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z`. Models are separate at exact midpoint
leads 15h, 39h, and 63h, mapping to the 0–24h, 24–48h, and 48–72h buckets.
Family, station, measurement, and horizon populations are never pooled.

For current reviewed central forecast `F` and canonical selected historical
residuals `R = (r1,...,rn)`, the predictive sample is exactly `Xi = F + ri`
with uniform mass `1/n`. Predicate probability is the exact fraction satisfying
strict `GT`, strict `LT`, or inclusive `RANGE`. There is no rounding, smoothing,
clipping, tail extrapolation, winsorization, recency/seasonal weighting,
observed-temperature floor, or synthetic sampling. Empirical probabilities 0
and 1 are retained and labeled `EMPIRICAL_BOUNDARY_MASS`; they are not claims
of certainty or impossibility.

The pure loader verifies the complete coverage artifact's portable hash and
strictly deserializes selected residual dictionaries. It validates canonical
dates/timestamps and finite Decimal strings, selected identities, residual
arithmetic (`observed - forecast`), authority, family, measurement, exact
horizon, replay fidelity, uniqueness, order, research-only status, and zero
production influence. Raw rows never enter the model boundary.

Current forecasts must be reconstructed from and independently revalidated
against a complete three-record `RawGribEvidence`: exact 03Z TMAX, 2 m,
parameter 0/0/4, Forecast/Maximum process, 12-hour 9–21h/33–45h/57–69h
intervals, reviewed grid, exact 15h/39h/63h midpoint, and nonempty raw and
extraction hashes. A bare central temperature is not an input API.

The minimum is 365 eligible TRAIN rows per exact horizon plus support in every
calendar month represented by the locked TRAIN span. This is explicitly the
`V1_OPERATING_SAFETY_FLOOR`; it is not proof of independence, tail quality,
statistical adequacy, or settlement calibration.

## Frozen evaluation policy

- TRAIN: 2024-01-01 through 2025-06-30, inclusive.
- VALIDATION: 2025-07-01 through 2025-12-31, inclusive.
- FINAL HOLDOUT: 2026-01-01 through 2026-07-31, inclusive.

Membership uses local target date only. Validation walk-forward training is
restricted to the same exact horizon and target dates strictly earlier than
the evaluated target. Primary holdout evaluation freezes TRAIN + VALIDATION;
no holdout residual enters that model.

Primary performance is empirical-distribution CRPS versus the raw NDFD point
mass, whose CRPS is absolute forecast error. The model must have strictly lower
mean CRPS separately at all three horizons. Diagnostics include raw and
bias-corrected MAE, residual mean/median, 50%/80%/90% equal-tailed nearest-rank
interval coverage and width, and empirical boundary frequency.

Before holdout outcomes are evaluated, the split manifest freezes nine exact
two-sided binomial coverage acceptance regions: three horizons by three
interval levels. The family-wise alpha is `Fraction(1, 20)`, Bonferroni
adjustment is applied across `9` gates, the per-test alpha is
`Fraction(1, 180)`, and each tail alpha is `Fraction(1, 360)`. For
`K ~ Binomial(n,p)`, the exact-tail convention rejects a low count `k` when
`P(K <= k) <= 1/360` and a high count `k` when `P(K >= k) <= 1/360`;
equality is rejected. Exact `Fraction` finite sums produce the retained
integer interval `[k_min,k_max]`; no normal or Wilson approximation and no
runtime scientific dependency is used. The policy constants, gate count,
adjustment method, and tail convention are bound into split and evaluation
identity material.

## Settlement-source truth boundary

This output is only a **research-only GHCN-Daily physical-temperature proxy
probability for the exact authoritative contract predicate, conditional on
reviewed YGUZ98 forecast evidence**. It structurally carries:

- `claim_type = GHCND_PHYSICAL_TEMPERATURE_PROXY`
- `settlement_mapping_status = UNVALIDATED_GHCND_PROXY`
- `research_only = True`
- `production_influence = Decimal("0")`

Kalshi settles these contracts using The Weather Company; historical labels in
this model are GHCN-Daily `USW00014819`. No equivalence or discretization rule
has been established. The output is not a Kalshi/TWC settlement probability,
fair value, market-ready probability, edge, EV, signal, sizing, allocation, or
order input. It is not integrated with `Forecast`, independent probability,
Event Edge, market blending, portfolio, risk, or execution.

## Holdout execution incident and corrected replay

The first attempted final-holdout execution used TRAIN only because the runner
passed `training_end=TRAIN_END` to the residual-population loader. It produced
542 rows per horizon instead of the required artifact-specific 725 rows per
horizon through `VALIDATION_END`. The original evidence is preserved and
marked invalid as frozen-holdout evidence; it must not be used as pristine
holdout evidence.

The protocol-corrected replay used the exact TRAIN + VALIDATION manifest
populations. For this artifact, the corrected population count was 725 per
horizon. The corrected replay passed all three CRPS gates and all nine
coverage gates. No model or policy tuning occurred between the incorrect and
corrected runs. The corrected replay is useful non-pristine empirical
evidence only: 2026 is explicitly not a pristine holdout because its outcomes
had already been exposed, and it is not independently confirmed.

Local evidence hashes:

- Original incorrect run: `243714686a39f7c3f6dd31673d90f4581027503b1ffce8bb9c49d82309209520`
- Incident record: `10b4f352b8a0c6f470c36f31fd94a93ba66f5cf8cb4b1212011dc57e10bd268a`
- Protocol-corrected replay: `744b20bc17f59e6c64d10a8e35b73368c0c702bc9d162cc8ee78188312354f85`

Final-holdout execution is now being structurally hardened. The supported
evaluation boundary accepts a typed `WeatherResidualPopulation` and validates
its frozen TRAIN + VALIDATION identity, exact manifest membership, horizon,
dates, counts, research-only status, and zero production influence before it
constructs an empirical distribution. The bare-distribution evaluator is
private/internal only.

## Not yet accepted empirical performance

The 2026 period is not a pristine holdout because outcomes were already
exposed. This milestone does not state that the model is accepted, probability
is calibrated, or forecast skill is proved. The corrected replay is empirical
non-pristine evidence under the frozen manifest and gates, not an independent
confirmation.
