# KU-A4 — Modelability, First Non-Weather Recipe

## Canonical checkpoint result

**CPI RECIPE = DEFINED**  
**MODELABILITY = UNKNOWN**  
**EMPIRICAL EXECUTION = NOT PERFORMED**

This checkpoint is research-only. It grants no predictive-skill, economic-usefulness,
lifecycle, capital, risk, account, signer, credential, order, or execution authority.
`production_influence = 0`.

## Canonical predecessor

KU-A4 consumes only a canonical KU-A3.2 `EvidenceResolutionResult` and revalidates its
canonical identity before reading it. The exact predecessor posture is conserved:

- `EvidenceDomain = UNASSIGNED`;
- G1-G6 = `UNKNOWN`;
- G7 = `PASS`;
- `EMPIRICAL_ARTIFACT_UNAVAILABLE`.

A4 does not reconstruct A1-A3 authority and does not accept an A2.2 family, source title,
category, hostname, routing label, model output, fixture, current settlement row, or weather
proof as a substitute for the canonical A3.2 predecessor.

## Modeling primitive audit

Canonical main already contains useful structural primitives. A4 reuses their semantics
where appropriate rather than creating a second generic modeling framework:

| Primitive | Reuse in KU-A4 | Authority limit |
| --- | --- | --- |
| `ModelRecipe` | content-addressed structural recipe identity | registry/recipe validity is not modelability |
| `ReleaseTarget` | exact target identity; A4 requires `ReleaseTarget.CPI` | enum existence is not empirical evidence |
| `CalibrationMethod` | canonical calibration identity (`IDENTITY`) | no free-form A4 calibration authority |
| temporal train/validation/test contracts | design precedent for strict separation | weather calendar splitting is not reused as CPI evidence |
| settlement-label and training-manifest contracts | design precedent for future exact binding | present rows/manifests do not establish CPI truth |
| PIT feature validation and historical replay | design precedent for availability discipline | caller-asserted timestamps do not prove publication availability |
| probabilistic scoring and abstention | experiment-definition primitives | no skill or economic claim without real evidence |
| M28C tournament architecture | conceptual isolation/untouched-test precedent | concrete tournament/feature implementation is weather-specific and non-authoritative here |

No upstream primitive is hardened merely because it could be unsafe if consumed as positive
authority. A4 deliberately does not consume `ModelTournamentResult`, `ReleaseVintage`,
weather historical modeling, fixture/model-card output, current settlement rows, or registry
validity as modelability authority.

## Why CPI is the first recipe

CPI has the strongest existing non-weather structural basis in canonical main:

- `ReleaseTarget.CPI` exists in the scheduled-release interface;
- a transparent scheduled-release baseline shape already exists;
- canonical calibration, scoring, abstention, provenance, and temporal-isolation primitives
  can describe a conservative experiment;
- the existing CPI model card is explicitly shadow/fixture-backed and states that the real
  sample is insufficient.

This supports **recipe definition only**. It does not establish a CPI evidence domain,
source permission, historical labels, publication vintages, settlement finality, or empirical
model performance.

## Exact CPI recipe

The canonical A4 recipe is `CPI_INITIAL_RELEASE_TRANSPARENT`:

- exact target: `ReleaseTarget.CPI`;
- target: the initial published BLS CPI release value for the exact future A3-bound series,
  reference period, comparator, and contract semantics;
- revised/final CPI values may not substitute for initial-vintage PIT evidence;
- prediction cutoff must strictly precede the scheduled release;
- every feature must satisfy `feature_available_at <= prediction_cutoff` with independent
  availability proof;
- features: exact release calendar, latest three eligible initial CPI vintages, and PIT
  residual history from prior initial releases only;
- sample unit: one scheduled CPI release event, with sibling contracts grouped;
- split: release-publication-time, contiguous, walk-forward train/validation/test partitions;
- random splitting is prohibited;
- TEST information is prohibited from fit, calibration selection, hyperparameter selection,
  and abstention-threshold selection;
- baseline: unconditional prior-event base rate computed only from finalized labels available
  before each prediction cutoff;
- calibration identity: canonical `CalibrationMethod.IDENTITY`;
- abstention is explicit, including fewer than 12 eligible prior initial releases;
- deterministic policy: no stochastic fit and no random split.

The recipe itself is content-addressed, canonical-A4-issued, and validated by reconstructing
issuer-derived semantics before hash comparison. Recipe existence cannot promote M1-M6 or
overall modelability.

## Modelability requirements

| Requirement | State | Meaning / exact missing evidence |
| --- | --- | --- |
| M1 Exact domain binding | `UNKNOWN` | `MISSING:EXACT_A3.2_CPI_EVIDENCE_DOMAIN_BINDING` |
| M2 Settlement label definition | `UNKNOWN` | `MISSING:EXACT_SETTLEMENT_TARGET_DOMAIN_BINDING`; `MISSING:SETTLEMENT_CORRECTION_FINALITY_BINDING` |
| M3 Permitted feature sources | `UNKNOWN` | `MISSING:EXPLICIT_DOMAIN_SOURCE_PERMISSION` |
| M4 Historical label availability | `UNKNOWN` | `MISSING:REPOSITORY_CANONICAL_HISTORICAL_SETTLEMENT_TRUTH` |
| M5 PIT feature reconstruction | `UNKNOWN` | `MISSING:REPOSITORY_CANONICAL_POINT_IN_TIME_VINTAGES`; `MISSING:INDEPENDENT_RELEASE_PUBLICATION_AVAILABILITY_PROOF` |
| M6 Evidence-unit policy | `UNKNOWN` | `MISSING:REVIEWED_DOMAIN_EVIDENCE_UNIT_POLICY` |
| M7 Reproducible recipe | `PASS` — structural only | content-addressed deterministic experiment definition |
| M8 Temporal evaluation | `PASS` — structural only | release-time walk-forward, contiguous, no random split, TEST isolation |
| M9 Baseline comparator | `PASS` — structural only | explicit pre-cutoff prior-event base-rate comparator |
| M10 Calibration / uncertainty / abstention | `PASS` — structural only | canonical calibration identity and explicit abstention policy |

M7-M10 are properties of the experiment definition. They are not empirical proof and cannot
promote overall modelability while any of M1-M6 remains unresolved.

## Empirical posture

No empirical modelability execution occurs in KU-A4. The repository contains no canonical,
non-fixture CPI historical corpus bound to the exact A3.2 market/settlement semantics and no
A4 dataset, fit, scorecard, calibration result, or profitability result is produced.

`EMPIRICAL_ARTIFACT_UNAVAILABLE` is preserved.

The existing `ReleaseVintage` interface is not accepted as proof of publication time,
initial-vintage identity, replay availability, or revision lineage. Those facts require
independent canonical evidence before M5 can resolve.

Similarly, present settlement rows are not accepted as proof that correction, amendment,
dispute, supersession, and finality semantics are solved. That remains an explicit M2
prerequisite rather than an upstream refactor in this checkpoint.

## Economics / G6

KU-A4 inherits G6 from exact A3.2 evidence and does not rewrite it. Current canonical G6 is
`UNKNOWN`, so `economics_observability_state = UNKNOWN`.

No EV, profitability, after-cost edge, readiness score, family ranking, capital allocation,
promotion, or execution claim exists in A4. Structural recipe or model performance could not
change G6 even if such output existed.

## No upstream refactors

KU-A4 does not retrofit:

- `ModelTournamentResult` or weather tournament types;
- `ReleaseVintage`;
- weather historical modeling;
- settlement correction machinery;
- a second generic PIT framework;
- a second calibration, manifest, recipe, or evidence-domain framework;
- live acquisition.

Those systems are either design precedents or future evidence prerequisites, not positive A4
authority.

## Smallest next CPI evidence checkpoint

The next bounded prerequisite is a CPI historical evidence/domain-binding checkpoint that
positively and reproducibly binds:

1. exact Kalshi CPI market/series/contract semantics to the intended CPI initial-release
   target, unit, reference period, comparator, timezone, and settlement authority;
2. explicit source permission/governance for the exact historical CPI data source;
3. independently evidenced original publication/vintage availability, revision lineage, and
   replay timing for a sufficient historical release corpus;
4. canonical historical Kalshi settlement labels with correction/amendment/dispute/finality
   handling;
5. a reviewed release-event evidence-unit and sibling-contract dependence policy;
6. a PIT release-time dataset manifest with exact feature provenance and availability;
7. separately, the historical after-cost observability evidence needed to resolve G6.

Until those bindings exist, the canonical conclusion remains:

**CPI RECIPE = DEFINED**  
**MODELABILITY = UNKNOWN**  
**EMPIRICAL EXECUTION = NOT PERFORMED**
