# Requirements for a new prospective WeatherNext experiment

This is an unapproved research design checklist under Issue #234, separate
from the October evidence and the V3 generated-worker engineering harness.
It implements no model, score, economics calculation or trading decision.
**Live acquisition and scientific scoring are not authorized. Trading
authority remains NONE.** Engineering success cannot satisfy these requirements.

## Freeze before any target outcome can be learned

A new experiment needs a distinct ID, future target dates, cities/stations,
contract family and horizons, a complete prospective roster and immutable
authority boundary. Freeze exact model code/weights, source versions, feature
availability lags, ensemble-to-distribution mapping, calibration, all model
selection, bin/strike mapping, uncertainty, abstention and evaluation code.
Include runtime/dependency identities, source precision/units, revisions,
day/timezone definitions, station identity, contract-specific rules, equality,
rounding, revision and early-close semantics. A continuous p50 forecast alone
does not establish contract-event probabilities or their calibration.

The freeze must have independently attested pre-outcome chronology and raw
artifact hashes. An author-controlled Git date, later documentation or a
retrospective statement is not prior-registration proof. Require independent
review before collection/evaluation and a recorded amendment policy. A material
post-freeze change creates a new version with a fresh future cohort, rather
than rewriting the old registration. Neither October 3 nor failed October 4
captures can become prospective evidence through a new evaluator or engineering
repair. October custody and other unresolved gaps remain separate questions.

## Source and acquisition evidence

Any proposed live collection requires separate explicit approval after the
relevant engineering gates. Freeze exact authorized sources and locators,
generations, byte digests, permissions, rate budgets, allowed endpoints,
request/receipt timing, monotonic and UTC uncertainty, source publication versus
first-acquisition times, deadline/cancellation handling and create-once storage.
Plan for provider denials, stale or revised data, absent objects, incomplete
streams and missed slots. They produce recorded failures/abstentions, not
backfills or retrospective acquisition timestamps. A generated host probe is
not a release receipt, source-custody attestation or service-availability test.

Settlement labels require independent, finalized underlying provenance and
the correct source precision/rules for each contract. Do not infer a temperature
from a binary yes/no settlement. Rule changes and contradictory siblings
invalidate the affected evidence pending separate adjudication. Preserve the
entire preregistered denominator, including unavailable and conflicting labels.

## Side-specific executable observations and costs

Pre-freeze which YES and NO actions, quantity, order type and decision horizon
would be evaluated. Any later approved observational protocol must retain
time-aligned executable prices and depth on the exact side/action, quantity,
book sequence/epoch, freshness and validity status. Midpoints, last trades,
binary settlements and synthetic bid/ask complements do not prove that a
specified quantity was executable. Match all data to the contract lifecycle;
post-close quotes cannot establish executable opportunities.

Freeze the relevant fee authority and effective-time/version evidence,
contract-specific applicability, maker/taker treatment, rounding, precision,
quantity dependence and treatment of rebates. Do not assume a current generic
fee rule applies historically or prospectively unchanged. Use exact arithmetic
for later authorized economics. Explicitly account for spread, depth beyond
the best level, latency, slippage, partial fills, queue uncertainty, cancellations
and adverse selection. An unplaced maker quote does not establish a fill.
Missing cost, side-specific depth or execution evidence requires abstention;
zero-cost or guaranteed-fill substitutions are not acceptable.

No after-cost edge, profit, capacity or fill claim is derived in this gate.
Observed quotes alone would still not establish paper profitability. A later
approved execution simulator needs separately frozen assumptions and independent
validation, with optimistic/base/adverse scenarios clearly labeled and no
selection of assumptions after seeing outcomes.

## Abstentions, denominators and independent evaluation

Specify abstention rules before the cohort: ambiguous settlement, missing or
stale forecast/book/label, incomplete provenance, clock uncertainty, deadline,
missed slot, unavailable depth, fee ambiguity, uncertainty and changed rules.
Preserve every registered city-day, date cluster and decision slot, all negative
outcomes and all abstentions. Report coverage and missingness by reason; do
not silently restrict analysis to successful captures, liquid contracts or
favorable outcomes. Repeated snapshots and correlated cities are not independent
outcomes. Predefine clustering, sampling, multiplicity and any stopping rules.

Create chronological training/calibration/development/holdout partitions before
access to holdout outcomes, with independent custody and leakage checks. Freeze
baseline comparators and incremental-source ablations. Probability evaluation
needs preregistered proper scoring/calibration and uncertainty diagnostics,
not just continuous forecast error. Predefine what would constitute adequate
out-of-sample evidence and how missing labels affect inference. Thresholds and
comparators cannot be tuned on the final holdout and then called independent.

Have an independent evaluator run the exact pre-frozen implementation against
admissible, independently verified inputs. Audit pre-outcome prediction hashes,
source first-acquisition timing, settlement versions and all exclusions. Any
post-holdout diagnostic is labeled retrospective; any revised model is tested
on a new untouched future cohort. Keep forecasting skill, execution feasibility,
after-cost research results and production eligibility as separate decisions.

Scientific scoring requires a distinct explicit authorization after those
gates. Even scientifically valid prospective evidence would not authorize
account access, orders, scheduling, production influence or trading.
