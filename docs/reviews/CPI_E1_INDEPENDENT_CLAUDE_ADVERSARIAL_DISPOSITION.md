# CPI-E1 Independent Claude Adversarial Review Disposition

Status: **INCORPORATED AS ADVERSARIAL INPUT; NO EMPIRICAL ACQUISITION IMPLEMENTED**

Canonical base reviewed: `82b80d207e10a64c7f477f887887166634698487`

This disposition records how the independent Claude CPI-E1 adversarial review
changes the checkpoint. It is not independent empirical evidence, does not grant
source permission, and does not create G1-G5 PASS authority. Missing empirical
proof remains UNKNOWN. G6 remains UNKNOWN. A3.2/A4 consumption is unchanged.

The independent review is treated as an attack inventory, not as an instruction
to implement every proposed mechanism verbatim.

## Canonical verification results

The following high-value claims were verified directly against canonical source:

1. `services.forecasting.macro.ReleaseVintage` is an ordinary frozen dataclass
   with caller-provided `published_at`, `replay_available_at`,
   `revision_number`, and `source`. It is structural/model-fixture precedent
   only and must not be consumed as CPI empirical authority.
2. `services.historical_replay.domain.Availability` correctly separates
   observed-live, reconstructed-exchange, reconstructed-primary-source,
   reconstructed-external, and unknown availability and enforces basis-specific
   timestamp arithmetic. It remains a public structural dataclass; construction
   of an `Availability` value is not empirical authority.
3. `services.contract_intelligence.settlement` separates `SourceObservation`,
   `ExchangeDetermination`, and `SettlementRecord`. `SettlementRecord` permits a
   training label only with `finalized_at` present and reconciliation status
   `MATCHED`. CPI physical-source evidence therefore cannot substitute for
   exchange settlement truth.
4. Canonical determination semantics explicitly distinguish DETERMINED,
   DISPUTED, AMENDED, and FINALIZED states, and source/determination records have
   correction/supersession links. CPI historical settlement evidence must
   preserve these distinctions rather than accept a current settled snapshot as
   correction-safe truth.
5. `ResearchFamily.BINARY_THRESHOLD` is only a structural family. A2.2 and A3.2
   already conserve exact mapping identity, and A3.2 intentionally keeps its
   evidence domain `UNASSIGNED`. CPI-E1 must not add a family-wide CPI authority
   shortcut or modify A3.2 to create one.
6. `ContractSpecification` already preserves exact market/event/series tickers,
   rules and metadata identities, propositions, comparator, threshold,
   threshold unit, settlement authority/source records, rounding rules,
   revision/correction rules, strike fields, occurrence/deadline timing,
   semantic status, provenance, and semantic hash. These existing fields must be
   exhausted before CPI-E1 invents CPI-specific taxonomy.
7. Canonical contract parsing does **not** currently expose dedicated first-class
   fields for headline/core, seasonal-adjustment basis, or MoM/YoY identity.
   Whether those require a narrow CPI projection cannot be decided safely until
   an exact CPI cohort and its rule material are canonically acquired and bound.
8. `services.production_weather_strategy.forecast_vintage` demonstrates the
   correct split between ordinary exact source artifacts and separately issued
   historical publication proof. It also demonstrates non-public construction,
   issuer-derived final identities, exact source binding, and post-cutoff
   rejection. CPI-E1 may reuse that pattern, but not weather authority.
9. `services.forecasting.weather_source_authority` demonstrates repository-
   reviewed physical-source policy identity and caller-resistant construction.
   It does not grant BLS/CPI permission and must not be generalized into a
   caller-selectable source registry.
10. `services.agent_control_center.evidence_units` explicitly states that
    exchange-event identity is not statistical independence and ships with no
    real reviewed assignments. This supports release-event grouping as a G5
    policy shape, not a CPI G5 PASS today.
11. `services.production_weather_strategy.settlement_dataset` demonstrates a
    useful acquisition-bound pattern: exact response evidence, row-to-page
    containment, source-row hashing, semantic event grouping, and preservation
    of authority identities. It is weather-specific and cannot be promoted to
    CPI authority without a reviewed CPI source/mapping boundary.
12. A3.1 really does use `id(...)`-based predecessor object seals. A3.2's own
    `_a31_object_identity()` stores the bound object graph rather than raw IDs,
    while A3.2 also revalidates the canonical A3.1 result. Therefore Claude's
    broad description of an "A3.2 id()-based identity seal" is not copied as a
    CPI-E1 design requirement. CPI-E1 will reuse only the canonical
    predecessor-validation/object-binding pattern appropriate to the eventual
    interfaces.

## Attack disposition

### Adopt as mandatory fail-closed invariants

The following attack classes are accepted as CPI-E1 requirements whenever the
relevant future interface exists:

- title/category/series-name/hostname/source-string authority forgery;
- `BINARY_THRESHOLD` family-wide authority leakage;
- proof leakage between distinct mappings or markets;
- exact product/index, change basis, seasonal basis, reference period,
  publication identity, unit, comparator, threshold, rounding, settlement
  authority, and policy mismatches **when those dimensions are required by the
  exact supported contract**;
- physical BLS/source value substituted for Kalshi settlement truth;
- current source snapshot substituted for historical initial-vintage truth;
- caller-selected publication/replay timestamps or caller-selected "initial"
  / revision labels creating authority;
- scheduled release time substituted for proven actual publication
  availability;
- future or post-cutoff evidence;
- non-final, disputed, amended, unreconciled, or mismatched settlement evidence
  treated as a final label;
- sibling strikes treated as independent evidence units;
- duplicate/relisted source records silently double-counted;
- source records silently dropped rather than represented, aliased/grouped, or
  quarantined;
- direct construction, `dataclasses.replace`, `object.__setattr__`,
  mutate-and-rehash, exact-type substitution, and caller-selected provenance
  attacks against any future authority-bearing CPI object;
- fixture/synthetic evidence promoted into empirical PASS;
- missing proof converted to BLOCKED without positive blocker evidence;
- any G1-G5 result implying G6;
- any CPI-E1 object creating modelability, ranking, lifecycle, account,
  credential, signer, risk, execution, order, or production influence.

For every future authority-bearing CPI-E1 object, the accepted canonical shape
is: non-ordinary construction, internal issuance capability, immutable stored
representation, exact runtime type validation where type identity matters,
issuer-derived provenance and identity material, independently reconstructed
consumer validation, exact upstream binding, content-addressed identity,
mutation/re-hash rejection, `research_only = True`, and
`production_influence = 0`.

Python module privacy is **not** itself a security property. The authority
boundary must be the validated issuer/consumer construction and reconstruction
chain, not the fact that a capability name begins with an underscore.

### Accept the risk, but do not implement Claude's proposed mechanism yet

The following findings are valid concerns but Claude's concrete mechanism is not
adopted without exact-domain evidence:

1. **Headline/core, SA/NSA, MoM/YoY fields.** Do not add broad CPI enums merely
   because these dimensions can exist. First determine whether the exact
   canonical CPI contract rules plus existing `ContractSpecification` fields
   already bind the distinction. Add only the smallest missing structured
   dimension needed for the first supported domain.
2. **Reference-period/publication-period validation.** Require exact binding to
   the contract target/reference identity. Do not invent a generic "plausible
   CPI publication lag window"; canonical code contains no reviewed universal
   lag policy, and delayed releases make such a heuristic unsafe.
3. **Initial-vintage uniqueness.** Do not enforce a generic rule that exactly one
   caller-visible row per `(reference_period, series_id)` may ever have an
   "initial" label until the acquisition representation and correction model are
   independently established. The safe invariant is that callers cannot mint
   initial-vintage authority and that exact publication proof must identify the
   accepted original artifact/vintage.
4. **Annual seasonal-factor revisions.** Implement special handling only if the
   exact supported CPI domain uses seasonally adjusted historical values or
   another regime where later factor updates can alter the purported original
   vintage. Otherwise document irrelevance and do not build generic machinery.
5. **Sibling statistics tests.** CPI-E1 must establish grouping authority, but it
   does not implement model statistics. Do not add speculative statistical
   functions/tests solely to exercise a future A4 consumer.
6. **Relist/dedup behavior.** Preserve every source record. Equivalent/relisted
   records may be grouped or aliased under an evidence-unit identity, but must
   not be silently merged out of the corpus.
7. **Cross-sibling feature leakage.** Preserve exact availability and shared
   evidence-unit identity so a later model layer can enforce the rule. Do not
   implement model-feature or cutoff behavior outside CPI-E1.
8. **Identity seal mechanism.** Do not clone an `id()` scheme reflexively. Reuse
   the established predecessor validation/content reconstruction/object binding
   that actually fits the final CPI issuer/consumer chain.

### Defer until the independent Codex acquisition-feasibility audit

No empirical acquisition implementation or source-specific runtime authority is
committed before the independent Codex acquisition-feasibility audit returns.
The following requirements depend materially on the real acquisition surface
and therefore remain design constraints only:

- exact BLS product/interface permission and fixed-origin/path policy;
- how original archived publication bytes are identified and persisted;
- how actual publication availability is independently proven;
- how initial versus later/revised artifacts are distinguished from source
  evidence rather than caller labels;
- whether the selected domain requires seasonal-factor vintage handling;
- exact Kalshi historical settlement/finality/correction acquisition;
- content-store round-trip proof for persisted raw artifacts;
- the declared acquisition scope and its expected eligible source-record set;
- parser quarantine categories and source-record conservation manifests;
- duplicate/relist aliasing based on actually observed exchange identities.

## Corpus completeness rule

CPI-E1 must never assume a hard-coded historical count such as "240+ CPI
months." Completeness is relative to an explicit acquisition-scope identity and
its positively defined expected source-record set.

Within one declared scope, every expected source record must be conserved into
exactly one explicit disposition such as eligible/parsed, grouped/aliased,
unavailable-with-evidence, or quarantined. Conservation must be checkable by
exact identities, not by comparing only aggregate counts.

Until that expected set exists, corpus completeness is UNKNOWN rather than
BLOCKED or PASS.

## Structural schema decision after independent review

**No new CPI-E1 runtime schema is added in this continuation.**

That is deliberate, not an omission. The exact empirical cohort has not yet
been canonically acquired, BLS/CPI source authority is still absent, and the
pending Codex acquisition-feasibility audit may materially determine which
source/interface and publication-proof fields are real rather than hypothetical.
Creating CPI-specific enums or authority-looking objects now would risk encoding
an unverified external-data assumption or creating a parallel trust boundary.

The later minimum schema is constrained as follows:

- bind exact upstream A1/A2/A2.2 identities and the exact canonical
  `ContractSpecification` semantic/rules identity;
- represent only those CPI domain distinctions the exact contract requires and
  existing canonical semantics do not already express;
- bind validated `Availability` structurally inside capability-issued evidence,
  while requiring separate issuer-validated publication/source proof for G4;
- keep BLS/source observation, Kalshi determination, and finalized reconciled
  settlement label as distinct evidence layers;
- preserve acquisition scope, source-record identity, quarantine/alias outcome,
  and release/evidence-unit grouping identity;
- issue no G6, modelability, lifecycle, execution, risk, account, credential,
  signer, or order authority.

## Gate impact

The independent review does not change the current gate conclusions:

- G1 exact settlement/domain binding: **UNKNOWN**
- G2 permitted source: **UNKNOWN**
- G3 historical settlement truth: **UNKNOWN**
- G4 original publication/vintage/PIT: **UNKNOWN**
- G5 evidence-unit policy: **UNKNOWN**; one-release-event grouping remains the
  policy candidate, not empirical PASS
- G6 economics observability: **UNKNOWN**

No positive blocker evidence was added. Absence of proof remains UNKNOWN.

## Next allowed move

Keep PR #101 draft and evidence-only. Wait for the independent Codex
acquisition-feasibility audit before committing any BLS/Kalshi empirical
acquisition or source-specific CPI runtime authority.

After that audit, the smallest safe next implementation is whichever bounded
source-governance/schema prerequisite is positively supported by both canonical
contract semantics and the verified acquisition surface. Only then resume CPI-E1
G1/G3/G4 acquisition binding and G5 release-event grouping.

Do not modify A3.2/A4 consumption. Do not begin M28D-R2 or economics. Do not
begin execution/risk/account work.

CPI-E1 REMAINS FAIL-CLOSED PENDING BOUNDED SOURCE/ACQUISITION PROOF
