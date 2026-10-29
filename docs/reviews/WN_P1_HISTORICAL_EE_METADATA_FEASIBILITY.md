# WN-P1 — Historical Earth Engine metadata feasibility canary (research only)

**Status:** Bounded historical-only diagnostic. **Not** a live proof, prospective collector, model, paper signal, trade, or scientific-result issuer. Independent fresh-detached review required.

This checkpoint follows #238 and does not modify frozen October 2026 collection or settlement evidence, and does not depend on PR #237 for runtime. It reads only Earth Engine *image metadata*, not forecast pixels or Kalshi markets. This project has **no approved five-city The Weather Company settlement probability distribution**.

## Exact scope and interpretation

- Asset: `projects/gcp-public-data-weathernext/assets/weathernext_3_0_0_0p05deg`.
- Filter: specified **historical** target date's 00:00 UTC initialization, forecast hours 1..31.
- City windows: Boston and Miami [5..28], Denver [7..30], LA and Seattle [8..31] UTC forecast-hour labels, 24 consecutive hours each. These correspond to the frozen five-city CLI *fixed local standard time* design, not automatically the wall-clock DST day.
- Required band names: `station_head_temperature_2m_{mean,p10,p25,p50,p75,p90}`, all six must be named. Band presence is checked only on the first returned image; per-image bands and pixel fidelity are NOT proven.
- Expected `start_time` equals the target date's exact 00:00Z init, `end_time` equals init plus forecast_hour. Ingestion property `ingestion_time_utc` must be strictly before 09:00Z on the same target date; missing or later required hours cause a per-city abstention, not retry/backfill.
- The entire historical metadata JSON has a canonical SHA-256 digest for *internal comparison only*. It is caller-replayable; not evidence of a genuine contemporaneous acquisition, publication chronology, or GCS immutability. Source GET `ee.ImageCollection(...).aggregate_array(...)` uses one `ee.Dictionary(...).getInfo()` request but does not grant a retrieval-time receipt or independent source attestation.
- An optional local `--offline-json` input is **untrusted** fixture-only replay. The source path `--project` requires authorized EE login and the Python Earth Engine API; no credentials written or requested. First live attempt should use a finished historical target; it cannot establish an actual 09:00Z decision-time snapshot.
- EE marginal quantiles are not a *joint* 24-hour distribution. In particular `max(hourly_p50)` is not `median(max hourly temperature)`, and `max(hourly_p90)` does not license a 90% daily-max claim. Absolutely no Kalshi event probability, fees, EV, Brier, P&L or trade is issued.

Run with an explicitly authorized EE project and a historical target, **only after source/usage terms are assessed**:

```bash
python scripts/audit_wn_p1_source_metadata.py --historical-target 2026-10-27 --project total-market-138523
```

Exit 0 means all five windows pass the **retrospective metadata** check, exit 1 means some hours were absent/late, exit 2 means invalid input/source error. Any exit-0 `HISTORICAL_METADATA_PASS_NOT_SOURCE_PROOF` explicitly cannot certify live accessibility, contemporaneous acquisition or model adequacy.

## Acceptance before even a paper-only WeatherNext experiment

1. Independent exact-head CI and hostile read-only review of this utility. No merge before PASS.
2. Real EE historical metadata canary with valid rights and modest observed expense, verified source payload and timestamp; retain failure as failure. Do not retest October as if it were prospective.
3. A separate actual future point-in-time **predecision** issuer with a create-only ledger, source-proof ingestion and 5-city 24h roster, ordered acquisition vs decision-time checks, no retries/backfills; live image readiness and cost cannot be proven by historical metadata.
4. A defensible frozen TWC-settlement daily-high probability model, not derived ad hoc from hourly p50/marginals or outcome-exposed October.
5. Independently validated, conservatively fee-inclusive Kalshi order-book economics, then settlement reconciliation and date-cluster evaluation. Zero live orders, zero production authority.

Research limitations and first-party WeatherNext source URLs are documented in #238. Source rights and 08:10 UTC nominal arrival timing are not availability guarantees.
