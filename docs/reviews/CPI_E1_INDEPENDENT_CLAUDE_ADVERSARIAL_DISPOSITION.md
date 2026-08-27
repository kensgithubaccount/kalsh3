# CPI-E1 Independent Claude Adversarial Review Disposition

Status: **INDEPENDENT ADVERSARIAL AND ACQUISITION-FEASIBILITY REVIEW INPUT INCORPORATED; NO EMPIRICAL ACQUISITION IMPLEMENTED**

Independent-review canonical base: `82b80d207e10a64c7f477f887887166634698487`

Continuation base after external merge of CPI-E1 audit PR #101:
`e4112ff8d39fb97957f52e7eb39e435887f82cec`

This disposition records how the independent Claude CPI-E1 adversarial review
changes the checkpoint and how the subsequent independent Codex acquisition-
feasibility/G3/G4 closure review resolves the prior design hold. Neither review
is independent empirical evidence, grants source permission, or creates G1-G5
PASS authority. Missing empirical proof remains UNKNOWN. G6 remains UNKNOWN.
A3.2/A4 consumption is unchanged.

The independent reviews are treated as adversarial/design input, not as an
instruction to implement proposed mechanisms verbatim and not as substitutes for
canonical empirical evidence.

## Canonical verification results

1. `services.forecasting.macro.ReleaseVintage` is an ordinary frozen dataclass
   with caller-provided `published_at`, `replay_available_at`,
   `revision_number`, and `source`. It is structural/model-fixture precedent
   only and must not be consumed as CPI empirical authority.
2. `services.historical_replay.domain.Availability` correctly separates
   observed-live, reconstructed-exchange, reconstructed-primary-source,
   reconstructed-external, and unknown availability and enforces basis-specific
   timestamp arithmetic. It remains structural; construction of an
   `Availability` value is not empirical authority.
3. `services.contract_intelligence.settlement` separates `SourceObservation`,
   `ExchangeDetermination`, and `SettlementRecord`. A training-eligible
   `SettlementRecord` requires finalization plus `MATCHED` reconciliation.
   Physical CPI evidence therefore cannot substitute for Kalshi settlement
   truth.
4. Canonical determination semantics distinguish DETERMINED, DISPUTED, AMENDED,
   and FINALIZED states and include source/determination supersession links.
   CPI settlement evidence must preserve this lifecycle.
5. `ResearchFamily.BINARY_THRESHOLD` is structural only. A2.2/A3.2 conserve
   exact mapping identities, and A3.2 intentionally remains `UNASSIGNED`.
   CPI-E1 must not add a family-wide CPI authority shortcut or modify A3.2.
6. `ContractSpecification` already preserves market/event/series tickers, rules
   and metadata identities, propositions, comparator, threshold, threshold
   unit, settlement source/authority, rounding/revision/correction rules, strike
   fields, timing, semantic status, provenance, and semantic hash. These fields
   must be exhausted before adding CPI-specific taxonomy.
7. Canonical contract parsing does not currently expose dedicated first-class
   headline/core, SA/NSA, or MoM/YoY fields. Whether a narrow CPI projection is
   required cannot be decided safely until an exact CPI cohort and its rule
   material are canonically acquired and bound.
8. `services.production_weather_strategy.forecast_vintage` demonstrates the
   required split between ordinary exact source artifacts and separately issued
   historical publication proof. CPI-E1 may reuse the pattern, not weather
   authority.
9. `services.forecasting.weather_source_authority` demonstrates reviewed source
   policy identity and caller-resistant construction. It does not grant BLS/CPI
   permission and must not become a generic caller-selectable registry.
10. `services.agent_control_center.evidence_units` explicitly states that
    exchange-event identity is not statistical independence and ships with no
    real reviewed assignments. This supports a release-event grouping policy
    shape, not CPI G5 PASS.
11. `services.production_weather_strategy.settlement_dataset` demonstrates an
    acquisition-bound pattern: exact response evidence, row-to-page containment,
    source-row hashing, semantic event grouping, and preserved authority
    identities. It remains weather-specific.
12. Claude's broad description of an "A3.2 id()-based identity seal" is not
    exact. A3.1 does use `id(...)` predecessor seals. A3.2's own
    `_a31_object_identity()` stores the bound object graph and revalidates the
    canonical A3.1 result. CPI-E1 will reuse only the predecessor-validation /
    object-binding / issuer-reconstruction pattern appropriate to its eventual
    interfaces, not copy an `id()` mechanism reflexively.

## Mandatory fail-closed attack requirements

When the corresponding future CPI interface exists, CPI-E1 must reject:

- authority inferred from title/category/series name/hostname/source string;
- `BINARY_THRESHOLD` family-wide authority leakage;
- cross-mapping or cross-market proof reuse;
- mismatched CPI product/index, change basis, seasonal basis, reference period,
  publication identity, unit, comparator, threshold, rounding, settlement
  authority, or policy where the exact supported contract requires that
  distinction;
- BLS/physical-source values used as Kalshi settlement labels;
- current snapshots used as historical initial-vintage proof;
- caller-selected publication/replay timestamps, revision numbers, or `initial`
  labels creating authority;
- scheduled release time substituted for proven publication availability;
- future or post-cutoff evidence;
- non-final, disputed, amended, unreconciled, or mismatched settlement evidence
  used as a final label;
- sibling contracts treated as independent evidence units;
- duplicate/relisted records silently double-counted;
- unmatched/malformed records silently dropped rather than represented,
  grouped/aliased, unavailable-with-evidence, or quarantined;
- direct construction, `dataclasses.replace`, `object.__setattr__`,
  mutate-and-rehash, equal-valued string/`StrEnum` substitution, or
  caller-selected provenance creating canonical authority;
- fixtures/synthetic evidence promoted to empirical PASS;
- missing proof converted to BLOCKED without positive blocker evidence;
- G1-G5 implying G6;
- CPI-E1 creating modelability, lifecycle, account, credential, signer, risk,
  execution, order, or production authority.

Any future authority-bearing CPI object must follow the hardened canonical shape:
non-ordinary construction, internal issuance capability, immutable stored
representation, exact runtime type validation where relevant, issuer-derived
provenance/identity, independently reconstructed consumer validation, exact
upstream binding, content-addressed identity, mutation/re-hash rejection,
`research_only = True`, and `production_influence = 0`.

Python module privacy is not itself a security property. The authority boundary
is the validated issuer/consumer chain, not an underscore-prefixed capability
name.

## Valid risks whose proposed mechanism is not adopted yet

1. **Headline/core, SA/NSA, MoM/YoY.** Do not add broad CPI enums merely because
   these dimensions can exist. First determine whether exact contract rules plus
   existing `ContractSpecification` fields already bind the distinction. Add
   only the smallest missing field for the first supported domain.
2. **Reference period versus publication period.** Require exact target/reference
   binding. Do not invent a universal "plausible CPI publication lag" heuristic;
   no reviewed canonical lag policy exists and delayed releases make it unsafe.
3. **Initial-vintage uniqueness.** Do not encode a generic one-row-per-period
   rule until the acquisition/correction representation is independently
   established. Callers may never mint initial-vintage authority; accepted
   original-vintage identity must come from exact publication proof.
4. **Annual seasonal-factor revisions.** Build special handling only if the
   exact selected domain uses SA historical values or another regime where later
   factor revisions can change the purported original vintage.
5. **Sibling statistics / feature leakage.** CPI-E1 must preserve grouping and
   availability identities but must not add model statistics or feature
   consumers just to test future A4 behavior.
6. **Relists/dedup.** Preserve every source record. Equivalent/relisted records
   may share a grouping/alias identity but must not be silently merged away.
7. **Identity seal.** Do not clone Claude's proposed `id()` design. Use the
   established validation pattern that fits the final issuer/consumer chain.

## Independent Codex acquisition-feasibility disposition

The independent Codex acquisition-feasibility and G3/G4 closure review has now
returned against canonical
`e4112ff8d39fb97957f52e7eb39e435887f82cec`. It is review/design input only;
it is not canonical empirical evidence and does not itself grant source or gate
authority.

Its verified high-level disposition is:

1. `KXCPI` remains the strongest exact candidate for KU-A4: CPI-U, U.S. city
   average, all items, seasonally adjusted, signed one-month percentage change,
   a strictly-greater-than threshold family, with the Bureau of Labor Statistics
   as the named source.
2. Related series `KXCPIYOY`, `KXCPICORE`, and `KXCPICOREYOY` remain separate
   domains and cannot inherit `KXCPI` authority.
3. Historical Kalshi CPI market records are publicly obtainable. Public
   historical rows can provide market/event identity, exact rules,
   comparator/strike, result, settlement value, settlement timestamp, and a
   finalized status.
4. Public historical Kalshi rows do **not** establish a complete
   dispute/amendment/correction/supersession history. Therefore G3 remains
   **UNKNOWN**, and a finalized row alone must not become an eligible canonical
   training label.
5. Official archived BLS CPI releases can establish the exact historical
   **initial printed CPI value** needed by the `KXCPI` candidate.
6. The current BLS API cannot independently prove original historical
   initial-vintage/PIT truth.
7. Exact historical first-public server timestamp is not established by the
   reviewed acquisition surface.
8. KU-A4 does not require exact-to-the-second historical first-public timing.
   G4 therefore appears supportable through a separately reviewed,
   deterministic, conservative reconstructed-availability policy.
9. G4 remains **UNKNOWN** today. The remaining issue appears bounded to a
   policy/evidence prerequisite rather than an unavailable-data dead end.
10. The immediate next implementation checkpoint remains **CPI-E1-P1 — reviewed
    BLS CPI source governance**.
11. After P1, the likely next bounded checkpoint is **CPI-E1-P2 — conservative
    historical CPI PIT availability policy**.
12. Only after P1 and P2 should CPI-E1 resume bounded empirical acquisition and
    G1/G2/G3/G4/G5 evidence construction.
13. G6 remains entirely separate and **UNKNOWN**.

The prior Codex hold point is therefore closed. This does not authorize empirical
acquisition or runtime CPI evidence work inside PR #102 and does not begin P1.

## Corpus completeness rule

CPI-E1 must not assume a fixed historical count such as "240+ months."
Completeness is relative to an explicit acquisition-scope identity and a
positively defined expected source-record set.

Within that declared scope, every expected record must be conserved into an
explicit disposition such as eligible/parsed, grouped/aliased,
unavailable-with-evidence, or quarantined. Conservation must use exact identities,
not aggregate counts alone. Until the expected set exists, corpus completeness
is UNKNOWN.

## Structural schema decision

**No new CPI-E1 runtime schema is added in this continuation.**

The Codex feasibility review removes the acquisition dead-end concern, but it
does not create source authority or canonical empirical evidence. This PR remains
a review-record change only. BLS/CPI source governance is still absent, P1 has
not begun, and adding authority-looking CPI objects here would cross the explicit
checkpoint boundary.

The later minimum schema remains constrained to:

- exact upstream A1/A2/A2.2 identity plus exact `ContractSpecification`
  semantic/rules identity;
- only CPI distinctions required by the exact contract and missing from existing
  canonical semantics;
- validated `Availability` structurally embedded inside capability-issued
  evidence, with separate issuer-validated publication/source proof for G4;
- distinct BLS/source observation, Kalshi determination, and finalized reconciled
  settlement layers;
- explicit acquisition scope, source-record disposition, quarantine/alias, and
  release/evidence-unit grouping identities;
- no G6, modelability, lifecycle, execution, risk, account, credential, signer,
  or order authority.

## Gate impact

The independent reviews do not change current gate conclusions:

- G1 exact settlement/domain binding: **UNKNOWN**
- G2 permitted source: **UNKNOWN**
- G3 historical settlement truth: **UNKNOWN**
- G4 original publication/vintage/PIT: **UNKNOWN**
- G5 evidence-unit policy: **UNKNOWN**; one-release-event grouping remains a
  policy candidate only
- G6 economics observability: **UNKNOWN**

No positive blocker evidence was added. Absence of proof remains UNKNOWN.

## Next allowed move

PR #101 was merged externally after its original audit commit. PR #102 remains a
separate draft review change and must not contain P1 implementation.

Independent Codex acquisition-feasibility and G3/G4 closure review has now
returned. It confirms that bounded `KXCPI`/BLS empirical acquisition is feasible
in principle, preserves G3 as UNKNOWN, identifies a bounded conservative PIT
policy path for G4, and confirms **CPI-E1-P1 — reviewed BLS CPI source
governance** as the immediate next implementation checkpoint.

After P1, the likely next bounded checkpoint is **CPI-E1-P2 — conservative
historical CPI PIT availability policy**. Only after those reviewed prerequisites
should CPI-E1 resume bounded empirical acquisition and G1/G2/G3/G4/G5 evidence
construction.

Do not modify A3.2/A4 consumption. Do not begin M28D-R2/economics or KU-A5. Do
not begin model execution or lifecycle/execution/risk/account/credential/signer/
order work in this PR.

CPI-E1 REMAINS FAIL-CLOSED; NEXT IMPLEMENTATION CHECKPOINT IS CPI-E1-P1
