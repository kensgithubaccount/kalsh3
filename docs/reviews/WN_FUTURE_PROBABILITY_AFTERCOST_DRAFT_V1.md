# WeatherNext future probability and after-cost protocol — DRAFT V1

Parent Issue #234. Baseline `3cd2695592e10132ed54773ccb9d0fbfbcbe635f`.
Required baseline run `41888922778` passed all four jobs before implementation.
Reference: `WN_NEW_PROSPECTIVE_EXPERIMENT_REQUIREMENTS.md`, preserved unchanged.

**OFFLINE DESIGN ONLY. NOT REGISTERED. NOT FROZEN. NO EXPERIMENT ACTIVATED.**
The model, scientific evaluator, source custody and economic evidence described
below do not exist as approved artifacts. No October fitting/evaluation, live
forecast/quote/outcome acquisition, scheduling, scientific scoring or trading
is authorized. The rehearsal accepts synthetic fixtures only and cannot issue
scientific or execution authority. The machine-readable declaration and full
open checklist are `experiments/wn_future_probability_protocol_v1/draft_protocol.json`.

## 1. Distinct cohort, identities and times

Proposed ID: **WN-FUTURE-PROB-AFTERCOST-20270101-20270731-DRAFT-V1**.
Design as of November 4, 2026. Registration deadline December 15, 2026, 00:00Z.
Missing that deadline cancels this candidate cohort; it does not create a
retrospective registration or rolling date shift. A new future ID is required.

| Partition | Inclusive future dates | Date clusters | City-days | Purpose |
| --- | --- | ---: | ---: | --- |
| Training | Jan 1–Feb 28, 2027 | 59 | 295 | Only future training labels may fit parameters |
| Embargo | Mar 1–14 | 14 | 70 | Finalize labels and attest training weights by Mar 10 00:00Z |
| Calibration | Mar 15–Apr 14 | 31 | 155 | Fixed calibration algorithm only |
| Embargo | Apr 15–30 | 16 | 80 | Finalize labels and attest calibration by Apr 25 00:00Z |
| Untouched holdout | May 1–Jul 31 | 92 | 460 | Independent evaluation only after separate authorization |
| Total | Jan 1–Jul 31 | 212 | 1,060 | Includes all 150 embargo city-days |

Declare six sibling slots and two side alternatives per event: 6,360 contract
slots and 12,720 action alternatives. These are denominators, not claims that
future contracts exist. Maximum one selected one-contract action per city-day;
all twelve alternatives and all non-selections remain in the ledger. Embargo
rows are retained; any unexpected attempt/failure is not overwritten by an
embargo label. Five cities on one date are correlated, not independent trials.
Caller labels cannot convert a training, calibration or holdout date to embargo.

Proposed identities are copied from archived metadata without promoting it to
future proof. Station coordinates are longitude/latitude; grid points are
declared proposals requiring independent future source binding.

| City | ICAO / CLI | Series family | Station lon/lat | Proposed grid lon/lat | IANA zone; fixed standard offset |
| --- | --- | --- | --- | --- | --- |
| Boston | KBOS / CLIBOS | KXHIGHTBOS | -71.01056 / 42.36056 | -71.00 / 42.35 | America/New_York; -05 |
| Miami | KMIA / CLIMIA | KXHIGHMIA | -80.31639 / 25.79056 | -80.30 / 25.80 | America/New_York; -05 |
| Denver | KDEN / CLIDEN | KXHIGHDEN | -104.65622 / 39.84658 | -104.65 / 39.85 | America/Denver; -07 |
| Los Angeles | KLAX / CLILAX | KXHIGHLAX | -118.38889 / 33.93806 | -118.40 / 33.95 | America/Los_Angeles; -08 |
| Seattle | KSEA / CLISEA | KXHIGHTSEA | -122.31361 / 47.44472 | -122.30 / 47.45 | America/Los_Angeles; -08 |

Future event template is `{series}-{YY}{MON}{DD}`; future sibling tickers,
strikes and rule hashes are **unverified**, not fabricated. Only the exact
target event with a complete mutually exclusive/exhaustive six-sibling partition
may qualify. Otherwise preserve an event-wide abstention. No source/family
substitution or favorable-strike selection after looking at outcomes.

For target date D, declare one decision at **D 09:00:00Z** for every city.
Bind rules/lifecycle by 08:50Z, complete the D 00Z forecast by 08:55Z, seal the
model probabilities by 08:58Z, and use a source-received quote no older than
one second at decision. Quote time cannot be after decision. Maximum clock
uncertainty is 100 ms; require open, unfinalized contracts with at least 900
seconds until actual close. No late source, quote or missed slot is backfilled.
Postdecision book observations at declared execution horizons may only test
the already sealed action, never revise its probability/selection or timestamp.
Training dates preserve raw inputs and the M0 benchmark only; M1 does not yet
have fitted weights and cannot produce an M1 decision. Embargo dates permit
no experimental selection. M1 economic decisions are holdout-only; calibration
forecasts exist only to select the predeclared scale, not to claim expectancy.

Proposed 24-hour measurement windows are fixed-standard D 05Z to D+1 05Z
(Boston/Miami), 07Z to 07Z (Denver), 08Z to 08Z (LA/Seattle). This convention
is **not established for future TWC contracts**. DST wall-clock conversion
must not silently change it. If controlling rules require a different window,
resolve and register a new exact mapping before the registration deadline;
otherwise ABSTAIN. Do not reinterpret old October observations.

## 2. Inputs: demonstrated documentation versus unproven capability

The proposed primary source is model `weathernext_3_0_0`, variable
`station_head_temperature_2m`, Kelvin, full member IDs 0–63 and each of the
24 required hourly valid times, retaining temporal member identity. Exact
objects, path components, generations, grid indices and dimensions must be
bound before use; the declared GCS prefix is not an access receipt.

Google documents raw ensembles in [GCS](https://developers.google.com/weathernext/guides/gcs),
with `lead_time` and `lead_subtime` requiring reconstruction. The saved P2 audit
establishes a historical Chicago decoder boundary and a global-chunk/cost
problem; it does not demonstrate timely, affordable five-city access. Do not
raise or bypass the existing 2 GiB safety cap. Access rights, billing, byte
budgets, arrival, raw digests and custody all remain unresolved.

[Earth Engine](https://developers.google.com/weathernext/guides/earth-engine)
and [BigQuery](https://developers.google.com/weathernext/guides/bigquery)
document hourly summary statistics, not the required joint trajectories.
The old local p50 receipts and metadata checks prove neither joint daily maxima
nor current availability. There is no p50-to-distribution shortcut or silent
statistics-source fallback. A [nominal dissemination schedule](https://developers.google.com/weathernext/guides/dissemination)
is a planning estimate, not proof of arrival before our cutoff.

Settlement is also unresolved: [general Kalshi help](https://help.kalshi.com/en/articles/13823837-weather-markets)
describes NWS daily reports, while saved contract-specific rules name TWC.
The exact future contract must establish the controlling agency, station,
window, units, source precision and revision hierarchy. Do not resolve that
conflict by preference. No temperature may be inferred from a binary payout.
The explicit 13-row matrix `unresolved_sources.csv` records each capability,
gap and required proof; current live/scientific admission is **NO-GO**.

## 3. Candidate probability methodology

**Raw baseline M0:** compute each complete member's maximum over the exact
approved hours, then count membership in each verified settlement event.
This is an hourly-grid maximum proxy, not proof of the station's continuous
daily maximum. Member dependence is preserved; 64 members are not 64 independent
outcome observations. Missing members/hours, mixed initializations or unknown
daily-window/precision mappings invalidate the result; do not subset or renormalize.

**Proposed primary M1:** an EMOS-style distribution for the official target,
using all 64 member-max proxies. The proposed family is Gaussian
`Normal(mu,sigma_squared)`, with CDF `Phi((h-mu)/sigma)`; this assumption is
unvalidated and does not follow from p50 or two moments alone.
Candidate mean is `a_city + b*ensemble_mean`;
variance is `c + d*ensemble_variance`, with b,d nonnegative and c at least
0.01 square Fahrenheit. Proposed bounds are each city intercept [-30,30] F,
b in [0,2], c in [0.01,400] F squared and d in [0,4]. Ensemble variance is the
descriptive mean squared deviation with denominator 64, not an assertion of
independent members. Fit only on eligible future training dates with
date-equal/city-equal CRPS weighting. Proposed deterministic bounded optimizer:
L-BFGS-B, max 2,000 iterations, tolerance 1e-9, fixed starts (city intercepts 0,
b=1,c=1,d=1); no algorithm search on holdout. Non-convergence means ABSTAIN.
Exact bounds, implementation/library identity and handling of station/time
bias must be reviewed and attested before registration, not improvised here.
This approach is motivated by the [original EMOS paper](https://sites.stat.washington.edu/MURI/PDF/gneiting2005.pdf);
that paper provides no validation of this source/station/contract experiment.

Calibration is a single shared scale multiplier selected only on the calibration
partition from {0.5,0.75,1,1.25,1.5,2} using date/city-equal categorical log loss.
It multiplies standard deviation, so calibrated variance is kappa squared
times the fitted variance; mean is unchanged.
Ties choose closest to 1, then smaller. No city-specific calibration search.
Freeze fitted artifacts independently by the deadlines; no holdout updating.
The second baseline M2 is each city's training-only empirical target CDF,
mapped to each day's verified boundaries. It sees no calibration/holdout labels.

Alternative investigations, **not adopted fallbacks**: residual models trained
on future p50-proxy errors require a separate fitted predictive distribution;
they are not distributions inherent in p50. Multiple hourly quantiles still
require a justified temporal dependence model. Quantile interpolation, Gaussian
fits or max-of-hourly-quantile operations cannot manufacture that dependence.
Any alternative primary method requires a new independently attested pre-outcome
version/cohort, never selection after inspecting the holdout.

For each method, convert its predictive law through the exact approved source
publication/precision mapping to strict tails and inclusive range events. Do
not round a fractional forecast or observed underlying to force integer bins.
If the six rules leave gaps/overlap under full precision, ABSTAIN rather than
renormalize missing mass. The rehearsal uses an explicitly fictional integer
lattice and rejects fractional maxima; that is not proof of a TWC rounding rule.

Primary forecast loss is six-category Brier loss, summed over sibling bins,
averaged across the five cities then equally over dates. Log loss (including
infinite loss for impossible predictions), reliability bins fixed at tenths,
calibration-in-the-large/slope and rank/PIT diagnostics are secondary. Do not
silently clip zero probabilities or count six siblings as independent trials.
[Proper scoring theory](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf)
supports the metric choice; no actual scores are computed here. A market
comparator, if later registered, must use a complete timestamped coherent
probability projection; asks/midpoints alone are neither independent forecasts
nor executable after-cost baselines. It is descriptive, not a third primary test.

## 4. Executable after-cost design

Evaluate hypothetical **BUY YES one integer contract** or **BUY NO one integer
contract**, FOK limit only, no maker orders, sell/short, leverage or multi-leg
position. No order is submitted. If future venue/type semantics cannot be
verified, ABSTAIN; never replace FOK with IOC. Unexpected fractional/partial
execution conflicts with the primary one-unit FOK design and is retained as
unverified, with realized costs/exposure if later legitimately observed.

Require side-specific genuine contra depth for the entire unit, book epoch/
sequence, exact contract rules, freshness, open lifecycle and clock custody.
Kalshi's [bid-only book documentation](https://docs.kalshi.com/getting_started/orderbook_responses)
allows deriving a YES ask from a genuine NO bid, and conversely, with that
bid's actual quantity. Never invent a side from same-side bids, a displayed
last price, midpoint or absent size. Independently verify depth at the declared
arrival horizon; an initial snapshot cannot guarantee a later fill.

Bind the **future effective** fee schedule, series modifiers, maker/taker
applicability, precision/rounding, per-fill/batch rules and every other charge.
The [official PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf) and
[fee page](https://kalshi.com/fee-schedule) are documentation references, not
2027 fee authority. Search copies exposed differing effective versions; the
direct PDF reviewed was labeled July 7, 2026. No current coefficient or exemption
is assumed for this cohort. Missing/changed fees mean ABSTAIN. Proposed supported
fee cap is $0.05 per filled one-unit action; higher/unknown fees are unsupported.
With the $0.99 price cap, maximum supported loss is $1.04 per selected attempt.

Predeclare scenarios before outcomes:

| Scenario | Arrival latency | Adverse price reserve | Probability sensitivity |
| --- | ---: | ---: | ---: |
| BASE | 250 ms | $0.01 | 0 |
| CONSERVATIVE | 1,000 ms | $0.02 | -0.02 on selected-side lower probability |
| ADVERSE | 2,000 ms | $0.03 | -0.05 on selected-side lower probability |

These are design stress assumptions, not verified worst-case market bounds.
Stress prices must remain supported; compute maximum rounded cost over all
permitted cent prices, not an assumed monotone fee. Spread is paid through the
correct ask; do not double-count it as an additional independent fee.

Fill uncertainty is mandatory. No-fill contributes zero attempt utility, but
fills may be adversely selected. Do not multiply unconditional outcome probability
by fill probability as if independent. For selected-side probability lower p,
fill q and per-filled-unit cost c, the rehearsal checks the Fréchet lower bound
`max(0,p+q-1) - q*c`, minimized over the declared q interval including its kink.
Unknown fill interval [0,1], missing depth/arrival proof or unidentified model
uncertainty requires ABSTAIN. Observational orderbooks do not identify real
fill/outcome dependence without additional evidence; this gap can keep the
entire after-cost verdict INCONCLUSIVE. Synthetic q values establish no fill model.

Require every scenario's lower bound at least $0.02 per selected attempt.
Otherwise **ABSTAIN**, however attractive a model probability looks. At most
one action per city-day is selected by maximum robust lower bound, ties by
exact ticker then YES before NO. Keep all twelve alternatives. No outcome-based
selection, guaranteed fills, zero-fee substitution or paper-profit claim.

## 5. Evidence, uncertainty and separate verdicts

The preregistered ledger must preserve every date/city, all sibling/action
slots, missing source, skipped slot, failed acquisition, unsupported rule,
abstention, no-fill and correction. Original bytes, digests, immutable versions,
request/receipt UTC and monotonic timing, uncertainty, publication versus
first-acquisition time and selection must reconstruct the decision. A local
hash or author-controlled Git date is not independent pre-outcome registration.

Minimum complete clusters: training 50/59, calibration 25/31, holdout 83/92.
Fewer means INCONCLUSIVE and no deadline extension. These floors are not a power
guarantee. Require a blind independent power/design analysis before registration
for a minimum meaningful 0.02 Brier improvement; an inadequate design needs a
new future cohort before outcomes, not a relaxed post-hoc threshold.

Primary inference unit is target-date cluster, all five cities together.
Declare seven-day moving-block bootstrap, 10,000 replicates, seed 20261104;
retain serial dependence and city/sibling dependence. Validate assumptions
blindly; unsupported dependence makes the confirmatory result INCONCLUSIVE.
Two fixed comparisons, M1 versus M0 and M1 versus M2, use Holm familywise 0.05.
Both must reject failure to improve by 0.02; no model/horizon/city search.
Secondary metrics/subgroups are descriptive, not extra discovery claims.

Incomplete dates stay in the 92-date denominator. Six-category Brier lies in
[0,2], so a missing paired cluster difference uses worst-case +2 for the new
model in primary sensitivity bounds. Success must survive that bound, not
assume missingness is random. Conditional complete-case diagnostics are labeled.
Log loss is unbounded; absent labels do not become zero log loss or finite
imputed evidence. Future authoritative revisions are append-only and versioned.

Keep four separate verdicts: **FORECASTING SKILL**, **EXECUTABILITY**,
**AFTER-COST RESEARCH EXPECTANCY**, **PRODUCTION ELIGIBILITY**. Passing one cannot
promote another. After-cost research additionally needs at least 60 selected
one-unit attempts over 30 dates and ten per city, verified cost/fill assumptions,
and positive lower attempt-utility bounds for all three scenarios with
simultaneous one-sided familywise 0.05 control. All non-selected/abstained slots
remain zero-action slots, not omitted. Unknown selected utility uses the supported
loss bound rather than a favorable fill. No missing source is relabeled as success.
This is conditional research expectancy, never realized profitability.
Production eligibility remains NONE irrespective of research results.

## 6. Registration checklist and rehearsal boundary

The complete machine checklist remains OPEN. Before any cohort activation:

- Independently attest protocol/cohort, model/evaluator code, source versions,
  ensemble mapping, calibration algorithm, probability conversion and abstentions.
- Resolve all 13 source-matrix rows, especially full member access, contract
  authority/window/precision, future fees, depth and fill identification.
- Freeze dependency/optimizer identities, training/calibration exclusion and
  correction rules, power/inference method, baselines and all diagnostics.
- Segregate holdout custody; independently attest fitted artifacts on the two
  deadlines, before calibration/holdout respectively. No October training.
- Validate native host/transport safety, create-once all-attempt evidence and
  cost/retention rights; no retry, backfill, deadline renewal or substitutions.
- Obtain independent exact-head review/CI and distinct explicit approvals for
  live acquisition and, later, scientific scoring. Neither is requested here.

The new pure module tests only synthetic SIM-namespace inputs: full-member
coherence, joint versus marginal counterexamples, exact Kelvin conversion,
explicit integer-domain contract semantics, probability simplex, proper-score
arithmetic, cent-rounded hypothetical fees, fill dependence, scenario stress,
freshness/lifecycle/source/size abstentions and the entire draft denominator.
All returned result stamps are synthetic/unregistered with scientific/live/
trading authority false/NONE. No file/data loader, network, fit, real evaluator,
model weights, source collector, scheduler or order client is added.

**Design/rehearsal PASS, if independently obtained, is not registration or
scientific validation. Current source/activation verdict: NO-GO.**
