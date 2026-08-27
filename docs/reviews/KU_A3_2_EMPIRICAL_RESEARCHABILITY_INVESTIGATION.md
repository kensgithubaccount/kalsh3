# KU-A3.2 Empirical Researchability Investigation

Canonical base: `00c65c75a34b4b05dfa5d9d89215a6a8c37ffbbb`

## Decision

KU-A3.2 found no admissible repository-canonical evidence that can safely move G1-G6 from
`UNKNOWN` for an evidence-homogeneous domain while preserving the exact
A3.1 -> A2.2 -> A2.1 -> A1 binding.

G7 remains `PASS`. No gate becomes `BLOCKED` because no positive canonical blocker is
established. The checkpoint preserves `EMPIRICAL_ARTIFACT_UNAVAILABLE`, remains research-only,
and has production influence `0`.

The implementation therefore creates mapping-scoped A3.2 receipts. Every exact A2.2 mapping is
conserved, but its evidence domain is `UNASSIGNED` unless a positive reviewed domain binding exists.
No such binding exists at this canonical base. This prevents a broad A2.2 structural family from
inheriting a narrow proof.

## Canonical evidence inventory

| Candidate artifact / interface | Positively proves | Does not prove | Applicable evidence regime | Gates it could affect | Proof type |
| --- | --- | --- | --- | --- | --- |
| `services/forecasting/daily_temperature.py` / M27C daily-temperature authority | Exact reviewed current-rule grammar for 20 CLI daily maximum/minimum temperature identities, exact location/timezone authority, Fahrenheit unit, TWC source name, and RANGE/GT/LT predicate mapping when the complete route succeeds | Authoritative TWC value product, observation window, rounding, correction/revision behavior, historical truth, PIT availability, source permission | Current TWC daily-temperature contracts accepted by the M27C route | G1, potentially G5 | STRUCTURAL |
| `docs/reviews/M27C_DAILY_TEMPERATURE_CONTRACT_AUTHORITY.md` | Reviewed narrow M27C contract-authority boundary and 20-identity scope | Does not independently validate historical TWC values or make a broad threshold-family claim | Same M27C daily-temperature regime | G1 | STRUCTURAL |
| `docs/reviews/M27C_TWC_SETTLEMENT_MAPPING.md` | Canonically records `NO_AUTHORITATIVE_TWC_VALUE_EVIDENCE` and the exact unresolved TWC product/window/rounding/revision questions | Does not prove impossibility and therefore does not justify `BLOCKED` | Same M27C daily-temperature regime | G1, G3, G4 | STRUCTURAL investigation evidence; no positive gate proof |
| `docs/reviews/M27C_WEATHER_SOURCE_AUTHORITY.md` | Reviewed NWS/GHCN physical-source identity for the weather stack and explicit separation from final TWC settlement authority | NWS/GHCN are not final settlement authority; current GHCN does not prove historical visibility | M27C weather physical-evidence regime | G3, G4, G5 | STRUCTURAL |
| `docs/source_policy.md` | Global policy: official/explicitly permitted interfaces only; authenticated products must not be scraped | Does not specifically permit a historical TWC, BLS, BEA, EIA, FRED, or other domain path; hostname/name alone is not permission | Global governance only | G2 | STRUCTURAL |
| `services/production_weather_strategy/settlement_dataset.py` / M28B | Acquisition-bound exact Kalshi historical settlement-row/label interface, fixed-origin public path policy, exact current TWC weather grammar, content-addressed provenance, and fixture-vs-acquisition separation | No committed non-fixture empirical settlement dataset at this checkpoint; cannot prove historical TWC measurement truth or PIT source values | Current TWC daily-temperature label acquisition boundary | G1, G3, G5 | STRUCTURAL interface; empirical PASS unavailable |
| `services/production_weather_strategy/forecast_vintage.py` / M28C | Explicit forecast-vintage identity and cutoff-safe visibility semantics | Does not establish that repository-canonical historical vintages exist comprehensively for an A3.2 domain | Weather forecast evidence | G4, G5 | STRUCTURAL interface |
| `services/production_weather_strategy/climate_evidence.py` / M28C | Explicit climate evidence/provenance boundary and weather physical-observation semantics | Physical observations are not TWC settlement truth and current/revised data do not prove PIT visibility | Weather climate evidence | G3, G4, G5 | STRUCTURAL interface |
| `services/forecasting/macro.py` | `ReleaseVintage` models scheduled/released/replayed timestamps, revision number, predecessor vintage, source, series, target, value, and visibility | Module describes fixtures; no committed canonical BLS/BEA/EIA/FRED release archive, source-governance binding, or exact A3 domain mapping is present | Scheduled macro/release model only | G3, G4, G5 | STRUCTURAL interface |
| `docs/reviews/M26G_REVIEWED_INDEPENDENT_EVIDENCE_UNITS.md` | Explicit reviewed evidence-unit semantics and complete-partition requirements | Canonical document states the real reviewed registry is empty; synthetic assignments are tests only | M26 evaluation evidence units, not an A3 settlement domain | G5 | STRUCTURAL; no A3 domain PASS |
| `docs/reviews/M6_historical_replay.md` | Replay architecture is cutoff/vintage aware, revisions are guarded, and unavailable historical fidelity fails closed | Historical Kalshi contract/market/trade/fill/order/candle reads are recorded as MOCK VERIFIED and human acceptance pending; does not prove a complete A3 domain archive | Generic historical replay | G3, G4, G6 | STRUCTURAL interface |
| `services/production_weather_strategy/historical_economics.py` / M28D-R1 | Exact historical quote/checkpoint evidence and temporally applicable reviewed fee-policy evidence, with no EV/PnL/execution authority | Does not complete M28D R2 or prove complete historical after-cost reconstruction for an A3 domain | Weather historical economics R1 | G6 | STRUCTURAL interface |
| `docs/reviews/M27A_LIVE_MARKET_ECONOMICS_COMPATIBILITY.md` | Current live economics replay interface, price ladders, exact book observations, current fee-resolution policy, research-only boundary | Explicitly does not reconstruct historical fees; final exchange fee can remain unknown; current live acceptance is not historical A3 evidence | Current live economics | G6 | STRUCTURAL/current empirical acceptance, not historical domain proof |
| committed test fixtures under `tests/fixtures/` | Exercise parser, vintage, replay, and economics behavior | Fixtures cannot establish empirical PASS by policy | Test-only | none | TEST ONLY |

## Evidence-domain investigation

### Candidate regime discovered: current TWC daily temperature

Canonical M27C/M28B evidence clearly describes a narrower evidence regime than the broad A2.2
`BINARY_THRESHOLD` and `BINARY_INTERVAL` families. However, the canonical A2.1 projection retained by
A3.1 does not preserve the M27C `DailyTemperatureRoute`, its complete rule text, reviewed M27C
`source_identity`, station/location/date semantics, or an equivalent reviewed domain-authority receipt.
A2.1 does retain structural comparator/strike/source projections, but reconstructing the M27C domain
from those partial fields would be a new semantic inference. Title, category, hostname, source name,
and M27B-style routing are explicitly insufficient.

Result: the candidate regime is documented but is not assigned as an A3.2 evidence domain. Runtime
A3.2 domain identity is `UNASSIGNED`.

### Scheduled macro / energy releases

`ReleaseVintage` is a useful PIT-safe structural model for CPI/PCE/payrolls/unemployment/GDP/claims/EIA,
but this repository snapshot contains no committed canonical release archive and no reviewed A3 domain
binding to BLS, BEA, EIA, FRED, or another source-specific release authority.

Result: no macro or energy A3.2 evidence domain is assigned.

## Gate resolution

Because no evidence domain can be positively bound through the exact A3.1 -> A2.2 -> A2.1 -> A1
chain, every mapping remains `UNASSIGNED`. The gate result is therefore identical for every A3.2
mapping receipt:

| Gate | A3.1 prior | A3.2 resolved | Proof | Exact unresolved evidence |
| --- | --- | --- | --- | --- |
| G1 — SETTLEMENT PROOF | UNKNOWN | UNKNOWN | none | exact settlement-target domain binding through the canonical A3/A2/A1 chain |
| G2 — PERMITTED SOURCE | UNKNOWN | UNKNOWN | none | explicit domain-specific research-source permission |
| G3 — HISTORICAL TRUTH | UNKNOWN | UNKNOWN | none | repository-canonical, non-fixture historical settlement truth for the exact target/domain |
| G4 — POINT-IN-TIME RECONSTRUCTION | UNKNOWN | UNKNOWN | none | repository-canonical historical vintages proving decision-time visibility without leakage |
| G5 — EVIDENCE-UNIT POLICY | UNKNOWN | UNKNOWN | none | reviewed admissible atomic evidence-unit policy bound to the exact A3 evidence domain |
| G6 — ECONOMICS OBSERVABILITY | UNKNOWN | UNKNOWN | none | complete historical after-cost observability evidence, including M28D R2 or equivalent completion |
| G7 — AUTHORITY ISOLATION | PASS | PASS | STRUCTURAL | none |

### State changes

- `UNKNOWN -> PASS`: none.
- `UNKNOWN -> BLOCKED`: none.
- `UNKNOWN -> UNKNOWN`: G1-G6 for every exact A2.2 mapping.
- `PASS -> PASS`: G7 for every exact A2.2 mapping.

Missing evidence is deliberately not converted to `BLOCKED`.

## Empirical-artifact posture

`EMPIRICAL_ARTIFACT_UNAVAILABLE` remains canonical. No fresh network or authenticated acquisition was
performed. No Kalshi, NOAA, BLS, BEA, EIA, FRED, TWC, or other external endpoint was called for this
checkpoint. Test fixtures remain behavior-only and cannot create empirical gate authority.

## A3.2 output and authority boundary

`services/market_universe/empirical_researchability.py`:

- consumes only a canonical `ResearchabilityHardGateResult`;
- validates the exact A3.1 object and nested A2.2/A2.1/A1 binding before issuance;
- emits exactly one A3.2 receipt for every exact A2.2 mapping, preventing family-wide inheritance;
- records family, mapping, source-record, A3.1, A2.2, A2.1, and A1 identities;
- records prior and resolved state for G1-G7;
- records exact missing-evidence references for unresolved gates;
- records proof kind as `STRUCTURAL` only for preserved G7 and no proof kind for unresolved gates;
- rejects `BLOCKED` without positive blocker evidence;
- rejects any G1-G6 promotion while the evidence domain is `UNASSIGNED`;
- rejects any weakening of G7;
- contains no network, account, credential, signer, risk, order, mutation, arm, burn, or final-acknowledgement dependency;
- contains no readiness score, ranking, EV, edge, profitability, lifecycle promotion, or execution authority;
- fixes `research_only == True` and `production_influence == 0`.

## Conclusion

KU-A3.2 does not have sufficient canonical evidence to promote G1-G6. This is the intended fail-closed
result. The repository now has a deterministic mapping-scoped resolution layer that can accept future
positive evidence only after an exact evidence-domain binding exists, without granting broad structural
families authority they have not earned.
