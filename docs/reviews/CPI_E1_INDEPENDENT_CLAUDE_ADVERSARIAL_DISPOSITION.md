# CPI-E1 Independent Claude Adversarial Review Disposition

Status: **INCORPORATED AS ADVERSARIAL INPUT; NO EMPIRICAL ACQUISITION IMPLEMENTED**

Independent-review canonical base: `82b80d207e10a64c7f477f887887166634698487`

Continuation base after external merge of CPI-E1 audit PR #101:
`e4112ff8d39fb97957f52e7eb39e435887f82cec`

This disposition records how the independent Claude CPI-E1 adversarial review
changes the checkpoint. It is not independent empirical evidence, does not grant
source permission, and does not create G1-G5 PASS authority. Missing empirical
proof remains UNKNOWN. G6 remains UNKNOWN. A3.2/A4 consumption is unchanged.

The independent review is treated as an attack inventory, not as an instruction
to implement every proposed mechanism verbatim.

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

## Deferred until independent Codex acquisition-feasibility review

No BLS/Kalshi empirical acquisition implementation or source-specific CPI
runtime authority is committed before the Codex acquisition-feasibility audit
returns. The following remain design constraints only until then:

- exact BLS product/interface permission and fixed origin/path policy;
- exact archived publication-byte identity and persistence mechanism;
- independently proven actual publication availability;
- source-derived initial-versus-later vintage identity;
- whether SA-factor-vintage handling is relevant;
- exact Kalshi historical settlement/finality/correction acquisition;
- content-store round-trip proof for persisted raw artifacts;
- declared acquisition scope and expected eligible source-record set;
- parser quarantine categories and conservation manifest;
- duplicate/relist aliasing grounded in observed exchange identities.

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

The exact empirical cohort has not been canonically acquired, BLS/CPI source
authority is absent, and the pending Codex feasibility review may determine
which source/interface and publication-proof fields are real rather than
hypothetical. Adding CPI enums or authority-looking objects now would risk an
unverified external-data assumption or a parallel trust boundary.

The later minimum schema is constrained to:

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

The independent review does not change current conclusions:

- G1 exact settlement/domain binding: **UNKNOWN**
- G2 permitted source: **UNKNOWN**
- G3 historical settlement truth: **UNKNOWN**
- G4 original publication/vintage/PIT: **UNKNOWN**
- G5 evidence-unit policy: **UNKNOWN**; one-release-event grouping remains a
  policy candidate only
- G6 economics observability: **UNKNOWN**

No positive blocker evidence was added. Absence of proof remains UNKNOWN.

## Next allowed move

PR #101 was merged externally after its original audit commit. This follow-up
must remain a separate draft review change.

Wait for the independent Codex acquisition-feasibility audit before committing
any BLS/Kalshi empirical acquisition or source-specific CPI runtime authority.
After that audit, implement only the smallest source-governance/schema
prerequisite positively supported by canonical contract semantics and the
verified acquisition surface.

Do not modify A3.2/A4 consumption. Do not begin M28D-R2/economics. Do not begin
execution/risk/account work.

CPI-E1 REMAINS FAIL-CLOSED PENDING BOUNDED SOURCE/ACQUISITION PROOF
