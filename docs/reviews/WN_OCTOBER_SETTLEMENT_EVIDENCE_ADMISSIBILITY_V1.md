# WN October C1: finalized-temperature admissibility desk audit (v1)

**Status (as of 2026-11-02 P0 operator adjudication): RESEARCH-ONLY / PARTIAL PROVIDER INVENTORY / SCORING INCONCLUSIVE–NOT COMPUTED / NO AUTHORITY.** The original October 30 documentary checkpoint is retained below for historical context; **section 6 supersedes its then-current evidence-status statements**. This PR does not import or independently validate the operator's local P0 artifacts.
This is a new prospective-to-retrospective evidence *admissibility specification*, not an outcome dataset, settlement claim, GCS audit, score calculation, or source request. It must not edit the frozen 2026-10-03..14 experiment, its sealed packets, the frozen evaluator, the WN-A2 Chicago reconciler, or any production/trading code. Parent issue #234; source/model issues #238/#242 and market-economics issue #244.

## 1. Why this gate is required

Merged PR #256 adds \`scripts/wn_multicity_october_offline_gate.py\`, independently reviewed at exact HEAD \`52beb3cb152663ed7371f40878c7d78c21b25937\` and merged to \`main\` at \`b29fc3b91bec731414efa3395bea740c066af8b5\`. The gate validates a **local operator-exported** C1 file structure, not that its bytes came from GCS at a specific generation, were originally acquired before the decision cutoff, or remain historically immutable. Self-consistent SHA256SUMS files are not outside-party provenance. A two-pass local filesystem inventory is not an atomic custody proof.

The **known retrospective candidate profile** is 12 target-date clusters, five frozen cities per date, 60 city-days total: 55 local capture candidates and five preserved October 4 \`DATA_NOT_READY_IMAGE_COUNT\` failures. Eleven date clusters could be forecast-complete *if* the 55 originals are independently attested; none is yet demonstrated here to have admissible finalized temperatures for all cities. Do not silently turn local structural eligibility into source attestation or 55 successful scored observations.

The existing \`services/forecasting/wn_multicity_evaluation_spec.py\` has already frozen \`score_point_forecast(SealedForecast, finalized_fahrenheit=Decimal)\` and \`aggregate_cluster_equal_point_scores(...)\`. **Do not modify these functions or create a new scoring formula.** The outcome acquisition, admissibility and full-denominator reconciliation needed before calling them are separate and not completed by this document.

## 2. Frozen event/station contract

Apply the exact D1 \`protocol.json\` and \`city_identity_amendment.json\`, not human-readable Kalshi titles. All targets are October 3 through October 14, 2026; each target-day window is **midnight inclusive to the next midnight exclusive in fixed local standard time**, not daylight-adjusted calendar midnight.

| City ID | Series | NWS daily CLI / station | Fixed-standard UTC window, D to D+1 |
| --- | --- | --- | --- |
| boston | KXHIGHTBOS | CLIBOS / KBOS | 05:00Z–05:00Z |
| miami | KXHIGHMIA | CLIMIA / KMIA | 05:00Z–05:00Z |
| denver | KXHIGHDEN | CLIDEN / KDEN | 07:00Z–07:00Z |
| los_angeles | KXHIGHLAX | CLILAX / KLAX | 08:00Z–08:00Z |
| seattle | KXHIGHTSEA | CLISEA / KSEA | 08:00Z–08:00Z |

The named settlement provenance in the frozen contract is the **Weather Company Kalshi Domestic Daily Climate relay of the specified NWS Daily Climate Report**. When a real Kalshi finalized-market response supplies the controlling numeric \`expiration_value\`, it may provide canonical settlement evidence only after matching this event/date/market/rules/source identity. A separate TWC/NWS CLI report, if obtained, can serve as corroboration or an explicitly separately reviewed *source-based* diagnostic; it cannot silently replace a conflicting Kalshi finalized result. Generic weather observations, WeatherNext model forecasts, public NWS proxies, scraped daily highs, and \`settlement_value_dollars\` ($0/$1 payout) are **not** acceptable substitutes for a full-precision settled temperature.

WN-A2's \`wn_a2_settlement_reconciliation.py\` is a **Chicago/WN-A1 persisted-attempt-specific** issuer using a frozen Chicago series/policy/zone. Its parsing and full-precision settlement comparator lessons can inform a *new*, independently reviewed multicity issuer, but its current \`SETTLED_MATCHED\` cannot be transplanted into these five cities or treated as evidence that any October C1 outcome was acquired.

## 3. Evidence admissibility, in fixed order

### A. Forecast pre-outcome custody (55 candidate packets)

For every expected city/date, bind the original \`packet.json\`, \`forecast.json\`, \`weather.json\`, \`market.json\` and \`SHA256SUMS.json\` to **source-derived object name, actual GCS generation/metageneration, content digest, storage creation/update metadata and independently observed acquisition/decision chronology**. Distinguish GCS object metadata time, embedded forecast receipt time, local export time, and observer time. A caller-authored JSON timestamp or a copied/self-rehashed local manifest cannot itself prove as-of availability or provider custody. Source generation metadata must come from an authenticated, read-only independent provider path or the result stays \`SOURCE_CUSTODY_UNVERIFIED\`.

Re-run the merged offline gate on a pinned local export without changing, refetching, repairing or completing historical forecasts. Do not trust gate status \`STRUCTURAL_PASS_NOT_ATTESTED\` as chronological validation. Reject post-decision acquisitions, identity mismatches, source ambiguities, duplicate/conflicting generations and any malformed or unavailable evidence. A source request or GCS read is **not performed or authorized by this PR**; separate scoped credentials/rights/cost approval remains necessary.

### B. Finalized outcome identity and acquisition

For each of the five named series and 12 target dates, independently bind the exact event date and all six sibling market identities/structured strike types, floors/caps, and the effective date-specific rules to the named TWC/CLI/station source. The candidate outcome acquisition must be **GET/read-only** through the already reviewed bounded public transport for actual Kalshi evidence, with path, HTTP status, exact returned raw response bytes, response-body SHA256 recomputed from those bytes, request start/end and local observation UTC, and source-reported market-settlement timestamp where present. Do not infer that an independently attested provider timestamp exists merely because a local clock says so. Historical-versus-current API choice must respect the exchange's public historical cutoff and be independently rechecked; do not reuse WN-A2 Chicago's hard-coded timezone choice for other cities.

Require an unequivocally \`finalized\` market with a **source-reported full-precision, finite Fahrenheit \`expiration_value\`**, matched event, rule version, city, station/CLI, date, and valid payout/result/settlement chronology. All accepted siblings that expose an underlying temperature must agree exactly. Any conflicting underlying, incomplete family, wrong target date/station/rules, still-open/preliminary market, $0/$1-as-temperature substitution, missing numeric underlying, or ambiguous settlement correction is **not an accepted numeric outcome**. Later controlling corrections must be append-only and explicitly versioned rather than silently overwriting earlier evidence.

Preserve the WN-A2 reviewed bucket semantics for separate rule diagnostics: bounded BETWEEN endpoints inclusive, LT and GT strict, and **no rounding** of source-reported precision. A bucket win or binary settled payout **cannot reconstruct the exact underlying temperature** when \`expiration_value\` is absent. The D1 conditional integer-domain amendment applies only to *exact source-reported integral* values; do not coerce nonintegral \`Decimal\` to integer. An independently reviewed new authority may be required for any novel source precision/domain.

### C. Deterministic roster reconciliation

Produce an append-only, reproducible audit row for **every one of the 60 frozen city-days**, including the five October 4 failed captures, without requiring every city to have a market result. Each row needs at least:

- exact \`city_id\`, target local date, station, CLI, event/series/market IDs, frozen protocol/amendment/image identities, one shared \`same_date_cluster\`;
- expected packet/failure state and source-custody verdict, original capture decision timestamp (if independently validated), outcome raw-body digest/source path/observed UTC, finalized timestamp (if valid), full-precision source value (if valid), rule/payout consistency state and any correction lineage;
- negative research-only authority and an explicit terminal reason if either side is missing, late, inconsistent, unattested or otherwise rejected.

A missing record, unknown status, conflict or unsupported value remains in the full denominator. **No synthetic or retrospective evidence object may receive an authoritative-positive verdict.** The five Oct 4 failures are never backfilled into forecast observations and never receive a forecast point-error score.

For clarity, use separate flags: \`FORECAST_STRUCTURAL_OK\` (local gate), \`FORECAST_SOURCE_CUSTODY_ATTESTED\` (independent original provider metadata), \`OUTCOME_FINALIZED_AUTHORITY_ATTESTED\` (raw finalized market or separately reviewed source authority), and \`POINT_SCORE_ELIGIBLE\` (both independent positive predicates). These are conceptual ledger fields, not implemented authority issuer values. Failure/unknown does not become \`False\` skill or zero error. The 12 target dates, rather than 60 city-days or 360 siblings, remain the primary dependence units.

### D. Only then: pre-frozen point-error evaluation

Pass the original \`SealedForecast\` and exact finite \`Decimal\` finalized Fahrenheit source value into frozen \`score_point_forecast\`; reject any fabricated forecast, rounded p50, settlement quantization, fitted bias or altered scoring rule. Aggregate with frozen \`aggregate_cluster_equal_point_scores\` over only **five-city fully eligible date clusters**, retaining partial-city diagnostics separately. The **maximum** potential complete cluster count from the observed capture profile is 11, not a claim that eleven eligible scored clusters exist. If no complete cluster has five admissible source-bound temperatures, the primary MAE/MSE/signed-error summary remains **INCONCLUSIVE / NOT COMPUTED**, not zero.

Do not use these October exposed results to train, tune or validate a retrospective six-sibling YES/NO probability model, Brier/log-loss superiority, fee-inclusive hypothetical profit, or forward executable capacity. Those require a new frozen, independently approved prospective model+market-economics+full-denominator ledger under issues #238, #244 and #245.

## 4. Mandatory negative/hostile cases for a future executable issuer

An independent fresh-detached reviewer should exercise: caller-controlled hash/receipt self-consistency but absent GCS source, forged/rewritten first-acquired dates or generations, missing/corrupt original raw bytes, spoofed event/date/CLI, wrong local standard versus DST calendar day, rules or strike-family mutation, binary payout treated as temperature, finalized YES/NO with nonmatching \`expiration_value\`, contradictory sibling underlying values, missing source precision, multiple corrections, preliminary versus final source, NaN/Infinity/Boolean-as-number, fabricated 55th capture, five Oct 4 backfills, cluster selection after outcomes, 12-cluster/60-observation independence inflation, scoring when one of five source values is unattested, nonatomic local export, and any model/probability/paper/trade promotion. Require one explicit negative/unknown verdict per affected expected row, never an omitted row or false \`PASS\`.

## 5. Original October 30 documentary decision and handoff (historical)

At this checkpoint, the repository contains an independently reviewed local October structural gate and a frozen deterministic point-error evaluator. It does **not** establish here a 60-row source-attributed October GCS evidence inventory or independently matched five-city finalized-temperature evidence. No cloud read or live public Kalshi/TWC/NWS request was made in this documentary checkpoint. Accordingly: **\`SETTLEMENT_ADMISSIBILITY_UNVERIFIED\`; frozen October point-error scoring remains BLOCKED, not a demonstrated absence or presence of forecasting skill.**

**Next separate reviewable implementation:** an *offline-only* deterministic five-city outcome/forecast admissibility and 60-row negative-status ledger using synthetic, expressly untrusted fixtures. It must not include a live GET, a model issuer, a paper candidate or a trade. A later, separately permissioned provider-read-only collection/custody phase can populate it if approved, with independent exact-head review before relying on positive source authority. The November 1 EE metadata one-shot is **NO-GO / NOT ARMED**; this audit cannot arm it.

**Freeze/promotion:** existing October sealed bytes, D1 protocol and WN-A2 Chicago source stay unchanged; \`production_influence=0\`, paper/trading/model authority \`NONE\`. CPI P12 is parallel, not a WeatherNext prerequisite.

## 6. November 2 P0 adjudication disclosure (post-checkpoint, research-only)

**Evidence status and provenance limits.** The operator reports an append-only local audit at \`wn_october_admissibility_audit_20261102T190000Z/\` and a separate P0 adjudication at \`wn_october_p0_adjudication_20261102T221037Z/\` with \`REPORT.md\`, a 60-row CSV, and \`SHA256SUMS.json\`. This PR does **not** incorporate those raw records, independently recompute their seals, or convert local/operator assertions into external provider custody. This section is a dated disclosure of their reported findings, not a new accepted evidence issuer.

**Updated observed coverage, not scientific admissibility.** The audit reports 1,670 GCS objects read with pinned generations, 55 structurally preserved captures, five October 4 failed captures, twelve target-date clusters, and 59 market-depth abstentions. Fresh finalized-market checks reportedly passed for 46 of 60 city-day families; fourteen families remain unverified following HTTP 429, with 19 HTTP 429 receipts preserved. A missing or rejected family is neither a temperature score nor evidence of poor forecasting skill; the full 60-row denominator must remain visible. GCS generation pinning strengthens current-object identity, but without independent original acquisition and clock custody does not, by itself, establish each source's historical first availability before the decision cutoff.

**Miami October 4 provenance conflict, UNRESOLVED.** Issue #234's October 28 comment records \`failure.json\` SHA-256 \`8cd4ad6e19605612abb4c390f930cb9d106f0113efc81e64acd1bc5a7ec6cc97\`. The operator's November 2 adjudication reports that GCS generation \`1791104405716513\`, metageneration \`1\`, provider MD5, original manifest and three SHA-256 implementations agree on the **new audit's** digest, but the historical issue hash has no independently bound original bytes or generation. The new digest is **not transcribed here** because the full local P0 manifest is unavailable in this PR. No conclusion about the historical hash's root cause is authorized. Preserve both claims, retain the October 4 failure state, and do not rewrite or backfill any original object.

**Score-method chronology must be evaluated per date.** GitHub history shows that October 3 revisions of \`services/forecasting/wn_multicity_evaluation_spec.py\` did **not** contain the current \`score_point_forecast\` and \`aggregate_cluster_equal_point_scores\` functions; they appear in commit \`7b330d56945278fdf1c0c38fdad729fde828daba\` (committer time 2026-10-04 14:52:32 UTC). The operator reports an October 4 17:33:44 UTC merge event binding the exact evaluator before October 5–14 targets. This PR does not independently attest an earlier exact-method freeze for October 3. Accordingly:
- October 3: **retrospective diagnostic only**; no proven prior exact-method scoring freeze.
- October 4: **five failed forecast acquisitions**, retained as terminal failures; no backfill or forecast point-error score.
- October 5–14: exact-method *prior-freeze chronology only* is reported conditionally established, **not** forecast custody, finalized-outcome authority, or overall point-score eligibility. Verify the controlling merge/tree and every independent source/clock gate before any scientific scoring.

**Test and review completeness.** The operator reports 71 focused tests PASS, 25 C1 PASS, and D1 seven PASS / **one missing-fixture ERROR**. The error remains unresolved; do not describe the complete D1 suite as PASS. Existing PR #257 CI 4/4 on its **previous** head is not an independent adversarial PASS and does not cover this addendum; require new exact-head CI and a fresh detached, independent review. Keep the PR **DRAFT / NOT MERGE READY** until that review passes.

**Mandatory ceiling.** No October scientific scores are computed or authorized by this amendment. The previous retrospective standalone point-error analysis remains descriptive only. No reconstructed October bucket probabilities, model fitting or calibration from exposed outcomes, fee-adjusted P&L, executable depth claim, trade or production authority. All original protocol, captured/failure objects, manifests, evaluator and source artifacts remain untouched; \`production_influence=0\`. All additional evidence corrections must be append-only and re-reviewed.
