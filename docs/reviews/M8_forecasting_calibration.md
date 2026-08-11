# M8 Forecasting + Calibration Review

## Acceptance

- Code, immutable artifacts, market references, independent/anchored separation, weather/CPI fixtures, replay gates, calibration, scoring, registry/cards, 5,000-row grouped evaluation and UI: **OFFLINE VERIFIED**.
- NWS and economic connectors: **MOCK VERIFIED** exact-host policies; live sources **NOT VERIFIED**.
- Production influence: **NONE**. Human acceptance: **PENDING**.

## Cross-functional findings

- **Quant / trader:** Market reference, executable bid/ask, independent fundamentals and anchored ensembles are separate artifacts. Independent schemas reject Kalshi and Polymarket features. Proper scores use the same checkpoint; no difference is labeled edge/profit/opportunity.
- **ML / data science:** Models, feature snapshots and calibrators are immutable/versioned. Fitting is chronological and event-grouped; small samples shrink or use identity. Counts distinguish forecasts, checkpoints, markets and events. Real evidence is insufficient and the UI says so.
- **Weather / macro:** Exact station, local timezone/day, units, comparator, rounding, source role and revision policy are required. Daily maxima respect observed floors. CPI uses initial point-in-time BLS-style vintages and a transparent residual distribution; live domain acceptance remains pending.
- **Data engineering:** Every forecast is content-addressed from frozen features, rules, source/evidence snapshots, calibration and code. Corrections/revisions are new records. Rendering reads persisted rows and never computes models.
- **Security / SRE:** Connectors permit only exact official hosts/paths, bounded read/query semantics and NWS identification. Forecasting has no signer, account gateway, risk authorization, mutation, sizing or execution imports. Family/source failures abstain independently.
- **Product / UX / CFO:** Market, independent and anchored values are labeled separately with intervals and sample state. No synthetic result is presented as real performance. Simple auditable baselines and caching/persistence precede expensive modeling.

No claim that a model beats the market or is profitable is supported by real settled data.
