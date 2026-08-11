# Weather empirical distribution v1

- **Intended use:** shadow research for validated daily maximum/minimum weather contracts.
- **Inputs:** exact station/date/timezone, authoritative point forecasts/observations, point-in-time residuals, M7-validated semantics.
- **Prohibited inputs:** Kalshi/Polymarket prices, order books, future observations, later corrections, settlement outcomes.
- **Method:** station residuals shrunk toward a pooled empirical distribution; observed daily maximum is a hard floor.
- **Calibration/uncertainty:** chronological identity or shrinkage calibration; empirical 90% outcome interval.
- **Required fidelity:** source issue/ingest time, local day, exact units/rounding and justified final label.
- **Limitations/failures:** ambiguous station/source/rules, outage, stale observation, unsupported measurement/rounding, insufficient residuals.
- **Status:** SHADOW. Real sample: insufficient. **PRODUCTION INFLUENCE = NONE.**
