# Contract Semantics R1.3 — reviewed comparison authority

This checkpoint is an offline, deterministic comparison repair. It does not
modify P9A/P9B evidence or P10A, and creates no execution, trading, capital,
credential, network, or production authority.

## Finite grammar and shared selection

`parse_comparison` accepts only complete, versioned full-match productions for
reviewed affirmative YES clauses, official/measured values, CPI settlement
sentences, reviewed titles, standalone comparison phrases, and exact inclusive
and signed-threshold variants. Each production consumes its complete clause
and binds polarity, comparator, threshold or bounds, inclusivity, reference
period where present, and affirmative YES payout orientation. Unsupported
residual prose, denial, modality, uncertainty, payout inversion,
contradiction, exceptions, malformed numbers, and non-finite numbers return
`Comparator.NONE`; there is no substring extraction, distance window, or
synonym blacklist.

`select_authoritative_comparison` is the shared path used by the specification
parser and router. It returns structured state:

* `MATCHED_RULES_PRIMARY` — a complete primary production is authoritative.
* `MATCHED_REVIEWED_TITLE_FALLBACK` — a reviewed title supplied the comparison
  because primary was absent or an approved frozen placeholder.
* `ABSENT_OR_APPROVED_PLACEHOLDER` — neither source supplied a comparison.
* `REFUSED_OR_AMBIGUOUS` — unsupported, contradictory, inverted, or
  conflicting material was refused.

The frozen P9A placeholder inventory is explicit: the empty primary value,
the exact historical CPI `|| percent ||%` sentence, and its exact
`0.50 percent%` sentence. Those two strings occur across three frozen rows
(`CPI-21OCT-T0.4`, `CPI-21OCT-T0.3`, and `CPI-21OCT-T0.5`). This is not generic
fallback: unknown nonempty primary text is refused. Title provenance remains
`title`, never primary.
When primary and title independently parse, comparator, threshold/bounds and
inclusivity must agree.

`rules_secondary` is never concatenated into primary parsing. It remains
available for family classification. A structured secondary clause is first
classified as one of: inert text; a complete reviewed comparison; or an
apparent but unsupported assertion. Bare `yes`, `no`, `resolves`, `settles`,
`pays`, `wins`, and `loses` words are inert. A complete reviewed comparison
must agree with the selected primary interpretation; malformed, negated,
payout-inverted, or contradictory threshold material fails closed. Router
blockers consume this same selection result and then compare its comparator to
the specification.

The frozen P9A replay has 473 nonempty secondary fields and zero secondary
classification changes: all are inert relative to the selected primary or
approved title fallback. This is an observed corpus result, not a claim that
all explanatory text is universally inert.

## Frozen replay and boundary

Unchanged P9A replay remains 60 independent events and 474 sibling markets,
with 267 two-sided usable rows and 148 fresh rows. Raw, semantic, and evidence
identities are not recollected or rewritten. The three historical placeholder
rows are governed only by the explicit frozen policy; arbitrary malformed
primary text cannot be rescued by a title.

This establishes only deterministic semantic parsing and router selection for
research/replay inputs. It does not establish external source authenticity,
outcome authority, predictive validity, profitability, fills, fees, after-cost
edge, model quality, or production readiness. Any live or prospective use
requires a separate reviewed authority.
