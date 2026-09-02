# Contract Semantics R1 — Negated Comparator Repair

## Scope

This offline, read-only repair makes the canonical contract comparison parser
polarity-aware. It does not modify P10A, P9A evidence, prices, outcomes, fees,
execution, risk, credentials, or production authority.

## R1.3 grammar boundary

Comparison acceptance is versioned by the parser's reviewed complete-template
family, rather than by searching a bounded character window. Each production
consumes the complete clause and binds polarity (affirmative only), comparator,
numeric threshold or bounds, any reference month/year, and the `YES` payout
orientation. The family is limited to: a comparison phrase alone; an explicit
`YES if` clause whose subject and unit are allowlisted; official/measured-value
clauses with the same allowlists; and the canonical CPI sentence `If the
Consumer Price Index (CPI) increases by ... (single-decimal) in <month> <year>,
then the market resolves to Yes.` Every token in the applicable clause must be
consumed by one of these productions. Payout orientation, denial, modality,
uncertainty, exception, conditional inversion, extra clauses, and unknown
wording therefore abstain as `Comparator.NONE`.

The subject and unit productions are intentionally finite reviewed vocabularies;
they are not a general natural-language or substring parser. A future exchange
wording requires a separately reviewed grammar production. Numeric parsing is
finite and deterministic, including signed negative thresholds; non-finite,
malformed, contradictory, or multiply interpreted clauses are rejected.

This is not a general natural-language parser. New exchange wording requires a
separately reviewed template before it can be accepted.

Supported complete phrases include affirmative `more than`/`greater than`
(`GT`), `less than` (`LT`), and the exact inclusive forms. The parser also
supports the logically exact negations `not/no more than` (`LTE`) and `not
greater than` (`LTE`), plus `not less than` and `not below` (`GTE`). `not
exactly`, double negation, unresolved negation, resolution-No clauses, and
contradictory or ambiguous candidate sets return `Comparator.NONE`. The
`ContractSpecificationParser` consequently emits its existing blocking
`UNKNOWN_LANGUAGE` issue and cannot mark such a specification strategy
supported.

Whitespace is normalized. Candidate spans and polarity are retained so an
inner generic phrase cannot override an enclosing supported negative phrase.
Multiple candidates are accepted only when their comparator, bounds, and
inclusivity are exactly equivalent; incompatible candidates fail closed.
Malformed month tokens attached to a comparison are rejected.

The residual guard also rejects contractions and auxiliary negation such as
`isn't`, `wasn't`, `cannot`, `can't`, and `won't`, as well as `neither`/`nor`
constructions. Straight and curly apostrophes are normalized, while numeric
hyphens remain intact so negative thresholds retain their sign. Bounded payout
direction phrases such as `NO wins`, `pays NO`, `the NO side wins`, `is
determined NO`, and `results in NO` make the comparison unsupported.

## Frozen-inventory impact

The canonical parser was replayed across the frozen P9A CPI inventory through
`validate_frozen_cohort`. Results remain exact:

- 60 events;
- 474 sibling markets;
- 267 two-sided usable quote rows;
- 148 fresh rows.

All stored comparator values, comparator symbols, thresholds, payout models, and
semantic hashes remain unchanged. No P9A frozen artifact hash was updated. No
non-CPI repository fixture was affected; the canonical contract-intelligence
and P9A replay tests pass against the unchanged fixtures.

## Verification boundary

This checkpoint establishes only polarity-aware deterministic comparison
parsing and preservation of the existing frozen evidence boundary. It does not
establish modelability, predictive signal, statistical significance, fills,
fees, after-cost edge, profitability, capacity, sizing, or production
readiness.
