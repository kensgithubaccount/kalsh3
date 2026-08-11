# CPI transparent scheduled-release model v1

- **Intended use:** shadow fixture-backed research for scheduled BLS CPI release-value contracts.
- **Inputs:** only initial vintages available at forecast time, release calendar, recent initial values and point-in-time residuals.
- **Prohibited inputs:** revised future vintages, Kalshi/Polymarket prices, hidden economist consensus, final settlement.
- **Method:** transparent recent-release baseline plus empirical residual distribution; no claim of predictive strength.
- **Calibration/uncertainty:** chronological identity or event-grouped shrinkage; empirical distribution.
- **Required fidelity:** release/vintage publication and replay availability, exact units, validated contract threshold.
- **Limitations/failures:** delayed/rescheduled release, missing vintage, unit conflict, fewer than 12 initial releases, API setup requirements.
- **Status:** SHADOW. Real sample: insufficient. **PRODUCTION INFLUENCE = NONE.**
