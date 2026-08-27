# CPI-E1 Historical Evidence & Domain Binding

Status: **BOUNDED EVIDENCE PREREQUISITE REQUIRED**

Canonical base: `82b80d207e10a64c7f477f887887166634698487`

This checkpoint is evidence-only and research-only. It does not modify A3.2 or
A4 consumption, does not establish modelability, does not execute a model, and
does not introduce market-side economics, lifecycle, account, credential,
signer, risk, order, or execution authority.

## Canonical primitive audit

| Primitive | Exact path/interface | Positively proves | Does not prove | Reuse conclusion |
| --- | --- | --- | --- | --- |
| A2.1 semantic/source coverage | `services/market_universe/semantic_source_coverage.py` | Exact upstream A2 record, capture, lifecycle, quarantine, source-record and semantic-coverage identity conservation | CPI domain semantics, BLS permission, historical finality, initial-vintage authority | Reuse unchanged as upstream identity input |
| A2.2 research-family mapping | `services/market_universe/research_family_coverage.py` | Exact A2/A2.2 identity conservation and structural family classification | CPI domain authority; `BINARY_THRESHOLD` is not a CPI proof | Reuse unchanged as structural input only |
| A3.1 hard-gate receipts | `services/market_universe/researchability_hard_gates.py` | Reviewed hard-gate state and exact upstream receipt binding | CPI empirical gates G1-G5 | Reuse unchanged |
| A3.2 empirical researchability | `services/market_universe/empirical_researchability.py` | Hardened capability-issued receipt mechanics and UNKNOWN/BLOCKED semantics | Any positive CPI evidence domain in v1; current domain remains `UNASSIGNED` | Do not modify in CPI-E1 |
| Contract specification | `services/contract_intelligence/specification.py` | Deterministic contract semantics including ticker/event/series, rules identity, proposition, comparator, threshold, unit, settlement source/authority, precedence and exception policies where upstream material supplies them | Empirical source permission, historical settlement finality, original-release vintage | Reuse unchanged for G1 inputs |
| Settlement domain | `services/contract_intelligence/settlement.py` | Structural distinction among determined/disputed/amended/finalized states and reconciliation eligibility | Historical correction-safe truth without independently acquired determinations | Reuse unchanged as semantics only |
| Public Kalshi read | `services/market_universe/public_read.py` | Fixed-origin unauthenticated bounded GET transport retaining exact response bytes/hash | Authority merely from successful HTTP; historical finality/correction history | Reuse for a later bounded acquisition only |
| Historical replay archive | `services/historical_replay/archive.py` | Content hashing and replay artifact persistence shape | Authority for caller-authored provider/timestamps/normalization | Reuse hashing/persistence conventions only; never promote caller-authored metadata |
| Historical replay availability | `services/historical_replay/domain.py` | Generic availability representation and replay filtering | Independently proven publication/replay timing | Cannot satisfy CPI G4 by itself |
| Macro release types | `services/forecasting/macro.py` | Structural `ReleaseTarget.CPI` and transparent-model recipe inputs | Source permission or trusted `published_at`, `replay_available_at`, `revision_number` | Do not treat `ReleaseVintage` as empirical authority |
| Weather source authority | `services/forecasting/weather_source_authority.py` | Repository-reviewed, capability-gated physical source mapping for the weather domain | Any BLS/CPI permission | Pattern is reusable; authority is weather-specific |
| Strict PIT vintage evidence | `services/production_weather_strategy/forecast_vintage.py` | Exact artifact binding plus separately capability-issued historical publication proof; replay-only artifacts cannot self-promote | CPI publication authority | Reuse the hardened issuance pattern, not the weather domain object |
| Historical settlement evidence | `services/production_weather_strategy/settlement_dataset.py` | Hardened exact-source/acquisition-bound historical settlement evidence for its reviewed weather scope | CPI settlement finality | Reuse the issuance and validation pattern, not weather authority |
| Evidence-unit partitioning | `services/agent_control_center/evidence_units.py` | Repository-reviewed event-to-unit partitioning and explicit dependence semantics | Any CPI assignment today; repository-reviewed assignments are empty | Reuse policy shape after CPI event authority exists |
| Source policy | `docs/source_policy.md` | Repository source-quality/governance guidance | Executable BLS/CPI permission | Insufficient for G2 without a reviewed code-level authority |

## CPI contract/domain taxonomy discovered

Discovery was deliberately non-authoritative. No discovery result below is
promoted into the canonical evidence corpus.

Public market material shows at least two semantically distinct nominal CPI
families:

1. `KXCPI`: a monthly CPI change threshold family. Current public rules describe
   a one-decimal monthly change and identify the Bureau of Labor Statistics as
   the verification source.
2. `KXCPIYOY`: a twelve-month CPI change threshold family. Current public rules
   describe a one-decimal year-over-year change and identify the Bureau of Labor
   Statistics as the verification source.

These families must not share positive domain authority merely because both are
called CPI. A supported domain still has to bind, from canonical contract
semantics, the exact population/series (for example CPI-U versus another CPI
series), all-items versus core, seasonal-adjustment basis, change horizon,
reference period, unit, rounding, comparator, strike, source precedence, and
amendment/correction/cancellation rules.

The candidate requested by KU-A4 remains **CPI INITIAL RELEASE**, but this audit
does not infer a positive evidence domain from the family name, title, category,
or public website text.

## Public official-source discovery

No canonical empirical acquisition was performed in this checkpoint.

A bounded public discovery pass established that BLS publishes:

- a CPI archived-news-release index;
- dated archived CPI release pages whose content represents the historical
  release and that explicitly warn later releases can revise data;
- a release calendar with scheduled date/time; and
- current/supplemental CPI materials that distinguish CPI-U, CPI-W and other
  representations.

This demonstrates that a later acquisition design has a plausible official
source substrate. It does **not** establish canonical permission. Successful
HTTP transport, a `bls.gov` hostname, `ReleaseTarget.CPI`, or a source name is
not authority.

## G1 — exact settlement/domain binding

**Conclusion: UNKNOWN.**

Canonical primitives can conserve exact A1/A2/A2.2 identities and parse exact
contract semantics, but CPI-E1 has not acquired a reviewable historical CPI
market cohort through those primitives. Public discovery indicates multiple
semantically distinct CPI families, so family-level or title-level fallback is
explicitly unsafe.

No market is silently dropped. The eligible CPI cohort is currently empty and
all discovered-but-unbound nominal CPI markets remain quarantined from positive
domain authority pending exact canonical acquisition and binding.

## G2 — permitted source

**Conclusion: UNKNOWN; bounded prerequisite identified.**

Canonical main contains no reviewed BLS/CPI source-authority object, registry,
or issuer. `ReleaseTarget.CPI`, hostname presence, contract text, source-policy
documentation, and the weather-specific source authority cannot confer BLS CPI
permission.

The smallest prerequisite is a **reviewed BLS CPI source-governance checkpoint**
that extends the repository's existing authority pattern without creating a
parallel generic trust system. It must positively bind only the exact BLS
product/interface required by a canonically proven CPI contract domain and must
remain `research_only = True`, `production_influence = 0`.

Until that prerequisite exists, CPI-E1 must not issue positive G2 evidence or a
positive top-level CPI evidence bundle.

## G3 — historical settlement truth

**Conclusion: UNKNOWN.**

The repository has structural settlement/finality types and hardened historical
settlement patterns, but no correction-safe empirical CPI settlement corpus is
present on canonical main. Present market state or a physical CPI value cannot
substitute for the exact Kalshi settlement label. A future CPI acquisition must
bind final outcome, settlement value where applicable, settlement timestamp,
exact response/evidence identity, and correction/amendment/finality semantics.

Missing proof remains UNKNOWN; no positive canonical blocker evidence was found
that would justify BLOCKED.

## G4 — point-in-time original CPI vintages

**Conclusion: UNKNOWN.**

`services/forecasting/macro.ReleaseVintage` is caller-constructible and permits
caller-provided publication/replay timestamps and revision numbers. It therefore
cannot prove an original historical CPI release vintage.

The strict `forecast_vintage.py` pattern demonstrates the required boundary:
an exact source artifact is replay-only unless independently bound to
capability-issued historical publication proof, and post-cutoff publication is
rejected.

BLS archived release pages make original-release reconstruction plausible, but
CPI-E1 did not acquire or persist exact raw historical release artifacts under a
reviewed BLS authority. No current BLS snapshot, revised/final value, or
caller-authored timestamp is accepted as historical initial-vintage proof.

## G5 — evidence unit

**Conclusion: UNKNOWN, policy candidate retained.**

The reviewed policy candidate remains one scheduled CPI release event as one
evidence unit, with sibling contracts grouped rather than treated as independent
samples. This is consistent with the repository's generic evidence-unit
principle that exchange-event identity is not statistical independence.

Positive G5 authority is withheld until an empirical CPI domain and exact event
cohort exist. The eventual policy must group multiple strikes and alternate
representations of the same CPI fact, handle duplicate/relisted markets and
corrections/amendments, and prevent sibling leakage across train/validation/test
splits.

## G6 — historical economics

**UNKNOWN, explicitly preserved.**

No executable edge, spreads, fees, slippage, depth, capacity, fill probability,
after-cost EV, profitability, or other market-side economics were calculated or
introduced. M28D-R2 remains separate.

## Evidence package / persistence conclusion

No positive CPI evidence package is issued because G2 lacks canonical source
authority and G1/G3/G4/G5 also remain empirically unproven.

Creating capability-looking CPI evidence objects before the BLS authority exists
would manufacture a new trust boundary and violate fail-closed source
governance. The repository's existing content-hash conventions remain available
for the later package, but hashes alone are not authority.

No empirical fixture, transient process object, or current network response is
represented as canonical empirical evidence.

## Adversarial boundary inventory

Because this checkpoint introduces **no new authority-bearing code type**, there
is no new reachable constructor/issuer boundary requiring a synthetic test
surface. The following attacks remain prohibited requirements for the later
source-governance/evidence implementation:

1. title/category/hostname creating CPI authority;
2. `BINARY_THRESHOLD` creating CPI authority;
3. authority leaking from one CPI contract to a semantically different CPI contract;
4. series/unit/comparator/reference-period mismatch;
5. core CPI substituting for all-items CPI;
6. SA/NSA substitution;
7. MoM/YoY substitution;
8. physical CPI value substituting for exact Kalshi settlement truth;
9. current BLS snapshot masquerading as an initial historical vintage;
10. revised/final value substituting for initial release;
11. caller publication/replay time creating PIT authority;
12. future publication;
13. post-cutoff evidence;
14. uncertain correction/finality becoming settlement PASS;
15. sibling contracts counted as independent samples;
16. duplicate/relisted markets silently double-counted;
17. malformed/unmatched markets silently dropped instead of quarantined;
18. fixtures/synthetic evidence creating empirical PASS;
19. caller-forged evidence objects;
20. direct construction of authority-bearing objects;
21. `dataclasses.replace`/`object.__setattr__` mutation creating authority;
22. mutate-and-rehash;
23. equal-valued string/`StrEnum` substitution where exact type identity matters;
24. altered upstream mapping/market/source identity;
25. altered source provenance;
26. altered vintage/revision identity;
27. altered label/finality identity;
28. altered evidence-unit membership;
29. missing proof becoming BLOCKED instead of UNKNOWN;
30. BLOCKED without positive blocker evidence;
31. changing `research_only`;
32. changing `production_influence`;
33. introducing G6/economics fields; and
34. introducing lifecycle/execution/account/credential/signer/order/risk authority.

## Changed scope

This checkpoint intentionally changes only this review artifact. It does not
modify workflows, A3.2, A4, modelability, execution, risk, accounts, or any
runtime authority surface.

## Smallest next checkpoint

**CPI-E1-P1 — reviewed BLS CPI source governance.**

Scope it narrowly to the exact BLS source product/interface required by the
first canonically bound `KXCPI`-class initial-release domain. Reuse the hardened
weather source-authority/PIT issuance patterns, but do not copy weather authority
or create a generic caller-selectable source registry. The prerequisite should
provide repository-reviewed, non-public construction/issuance and exact
source/product/interface identity with content-addressed policy identity,
`research_only = True`, and `production_influence = 0`.

After independent review of that prerequisite, resume CPI-E1 with bounded public
Kalshi + BLS acquisition, exact G1/G3/G4 bindings, and then empirical G5 event
partitioning. Do not modify A3.2/A4 consumption until the complete CPI evidence
package is positively valid.

CPI-E1 REQUIRES BOUNDED EVIDENCE PREREQUISITE
