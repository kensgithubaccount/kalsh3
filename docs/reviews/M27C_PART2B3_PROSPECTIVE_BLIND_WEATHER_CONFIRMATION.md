# M27C Part 2B3 — Prospective Blind Weather Confirmation Freeze

Part 2B3 predeclares `2026-09-01` through `2027-03-31` as the next genuinely
untouched confirmation period for the accepted research-only Chicago
`DAILY_MAX` physical-temperature proxy. The protocol is frozen before the
period begins at policy commit `76fbb8fea645bb2fffb91899eca4937bc17dac1d`.

August 2026 (`2026-08-01` through `2026-08-31`) is an operations-only blackout.
It is not available for fitting, validation, holdout evaluation, threshold or
policy selection, or statistical tuning. It may only exercise collection
machinery without model changes.

The frozen model population is exact TRAIN + VALIDATION membership through
`2025-12-31`, with training beginning `2024-01-01`. The three supported
midpoints are 15h (`54000`), 39h (`140400`), and 63h (`226800`). Their frozen
model identities are, respectively:

- `bb2758d3dbeac46b6fd92f7bde09549178ca2ac585f447d177cebc44fb758981`
- `bac5baa32d08c95fb2c3c78a587685d6e28e1f2ae6fa02f0b22029dac91e7a99`
- `15710083dfcf84d86443f5e1a5295c7fc000cdc54558b393cdbaf746adcbe811`

A rebuilt population that does not reproduce those identities fails closed.
The statistical policy remains family-wise alpha `1/20`, nine coverage gates,
per-test alpha `1/180`, two-sided tail alpha `1/360`, and Bonferroni adjustment.
Acceptance remains strict CRPS improvement at each horizon plus all nine exact
coverage gates. Future acceptance regions are generated from the eventual
exact prospective sample count under that frozen binomial policy.

The Jan–Jul 2026 corrected replay passed the frozen policy, but it is
non-pristine because its outcomes were exposed. It is not independent
prospective confirmation. No weather-model tuning is allowed during the
prospective period.

## Blindness boundary

During the period, collection is forecast-only. It may retain exact accepted
raw NDFD/YGUZ98 evidence, immutable provenance, source hashes, target dates,
midpoint and horizon semantics, collection timestamps, and missing-source or
quality metadata. The boundary rejects outcome, residual, CRPS, MAE, coverage,
evaluation-metric, market, and production-influence fields. The prospective
path does not import or call GHCN-Daily outcome acquisition.

Outcomes are intentionally deferred until after `2027-03-31`. The documented
post-period procedure is:

1. Freeze and hash completed forecast-only evidence.
2. Verify exact prospective target-date coverage.
3. Backfill GHCN `DAILY_MAX` outcomes.
4. Bind outcomes to the already-frozen forecast observations.
5. Construct the exact prospective evaluation manifest.
6. Compute the frozen CRPS and nine coverage gates once.
7. Preserve the result regardless of pass/fail.

That procedure is documented only; consequential evaluation is not implemented
in Part 2B3. Retrospective model modification cannot convert the period back
into a holdout.

Any passing result would establish only the
`GHCND_PHYSICAL_TEMPERATURE_PROXY` model. TWC settlement mapping remains
`UNVALIDATED_GHCND_PROXY`, and no trading claim follows automatically.
