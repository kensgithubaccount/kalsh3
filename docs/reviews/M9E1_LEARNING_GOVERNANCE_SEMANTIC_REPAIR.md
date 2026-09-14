# M9-E1 Learning Governance Semantic Repair Review

Bounded research/governance repair of `services/learning/evaluation.py` and
`services/learning/governance.py`. Production execution, credentials, risk limits,
trading activation, frozen GDP/weather semantics and existing promotion thresholds
are untouched. Production influence remains exactly 0. No scientific clock. No trading.

## Defects on `ca72cb2b38b2ea11f4f3650a9ad31d9b5c348d2f`

All four were reproduced against canonical main before repair:

| # | Defect | Observed on main |
| --- | --- | --- |
| 1 | Leave-one-event-out extrema treated as an inferential interval | 27 events at `+0.1` and 23 at `-0.1` returned `STRONGER_EVIDENCE` (point `0.008`, extrema `0.006122`/`0.010204`) |
| 2 | Duplicate `event_id`s inflate the denominator | 50 rows sharing one id returned `event_count=50` and `STRONGER_EVIDENCE` |
| 3 | All-negative contributions promote | 50 events at `-0.1` returned `STRONGER_EVIDENCE` (point `-0.10`) |
| 4 | `GovernanceProposal` trusts a supplied count; no independent effect requirement | A proposal claiming 50 events against a 3-event manifest was accepted, as was one built on the all-negative interval |

Defect 1 is the load-bearing one: leave-one-out extrema converge on the point
estimate as *n* grows, so they exclude zero whenever the mean is non-zero. They
measure influence of a single observation, not sampling uncertainty, and they
model neither dependence between events nor repeated looks.

## Repair

### `evaluation.py`

- `PerformanceInterval.lower`/`upper` renamed to `sensitivity_low`/`sensitivity_high`.
  The names were the defect's carrier; nothing may read them as interval bounds.
- Added `method` (`LEAVE_ONE_EVENT_OUT_SENSITIVITY`) and `inferential_method`
  (`None`). `__post_init__` rejects any `evidence` stronger than `INCONCLUSIVE`
  unless a prespecified `inferential_method` is named, so the constructor itself
  is the gate rather than any one call path.
- `paired_event_interval()` now always returns `INCONCLUSIVE`. `STRONGER_EVIDENCE`
  is retained as the state a future valid method must produce; it is currently
  unreachable from evaluation code.
- Duplicate `event_id`s raise `LearningError` rather than being silently collapsed:
  a repeated id is an unreconciled input, and collapsing would hide it.
- `minimum` defaults to `MINIMUM_UNIQUE_SETTLED_EVENTS = 50` and rejects any value
  below 50. The floor may be raised, never lowered. `meets_event_floor` records
  the outcome on the artifact.
- `positive_direction` is a descriptive direction diagnostic (point above zero and
  no single event flipping the sign). It is explicitly a necessary, never
  sufficient, condition.

### `governance.py`

- `GovernanceProposal` gains `event_manifest: tuple[str, ...]` — the exact distinct
  authoritative event ids — and `incremental_effect: Decimal`, the independently
  supplied after-cost / market-relative effect.
- Promotion now requires, in order: a duplicate-free manifest; `unique_settled_events`
  equal to the manifest length; the 50-event floor; an interval whose `event_count`
  matches the manifest; strictly positive `incremental_effect` *and*
  `interval.positive_direction`; `meets_event_floor`; and `STRONGER_EVIDENCE`.
  Every one fails closed.
- Direction is checked independently of evidence state, so a negative or flat
  result can never promote even if a future inferential method reports confidence.
- `compare_challenger()` rejects duplicate ids and intervals computed over a
  different event count, and additionally requires floor, direction and evidence
  state. It is presently always `False` by construction.

Non-promotion proposal types are unaffected; abstention and quarantine paths are
byte-for-byte unchanged in behaviour.

## Immutability

All records remain frozen slotted dataclasses; the regression suite asserts
`FrozenInstanceError` on both `PerformanceInterval` and `GovernanceProposal`.
No historical evaluation artifact was rewritten: the repository holds no stored
M9 interval or proposal records, and `migrations/0009_learning_governance.sql`
is unmodified, so existing rows keep their schema and values. The evidence-to-
proposal binding this repair enforces in code already exists at rest as
`learning_proposal_evidence`.

## Regressions

`tests/test_m9e1_learning_governance_semantic_repair.py` (9 tests):

- 27 at `+0.1` / 23 at `-0.1` cannot promote although both extrema exclude zero.
- 50 duplicate event ids cannot satisfy the 50-event gate, in evaluation,
  proposal and challenger paths.
- 50 all-negative events cannot promote, and a positive supplied
  `incremental_effect` cannot launder a negative observed direction.
- Manifest/count mismatch fails closed in both directions, as does an interval
  covering a different event set.
- Negative, zero and concentration-driven proposals cannot promote. The
  concentration case has a positive point estimate (`+0.00022`) whose sign one
  event flips (`sensitivity_low = -0.001`).
- `STRONGER_EVIDENCE` is unconstructible without a prespecified method.
- The 50-event floor cannot be lowered; raising it is allowed.
- Abstention/inconclusive behaviour is deterministic and order-independent.
- Artifacts are immutable and zero-influence.

`tests/test_learning_complete.py` was updated where it asserted the defect:
a uniformly positive 60-event sample is now `INCONCLUSIVE`, and
`compare_challenger()` on a strictly better same-event challenger now returns
`False`. Both changes record fail-closed behaviour, not a loss of coverage.

## Bounded authority-bypass repair (independent review, 2026-09-14)

Independent review reproduced two object-construction bypasses of the fail-closed
design above and returned **REPAIR REQUIRED**. Both are now closed.

**Blocker 1 -- caller-created `STRONGER_EVIDENCE`.** The prior `__post_init__`
checked only `inferential_method is not None`, i.e. it gated *presence*, not
*validity*, of the name. A caller could construct
`PerformanceInterval(evidence="STRONGER_EVIDENCE", inferential_method="arbitrary-caller-name",
...)` directly and have it accepted -- the "the constructor itself is the gate"
claim in the original repair section above was true of `paired_event_interval()`
but false of direct construction. Fixed: `__post_init__` now rejects *any*
`evidence` value other than `INCONCLUSIVE` unconditionally, and separately
rejects any non-`None` `inferential_method`. There is no accepted method to
validate a name against at this checkpoint, so no name -- however plausible --
can unlock `STRONGER_EVIDENCE`.

**Blocker 2 -- caller-authored `event_manifest`.** `event_manifest` was checked
only for internal self-consistency (no duplicates, length matches count and
interval), never bound to an authoritative evaluation cohort. Because Blocker 1
made `STRONGER_EVIDENCE` unconstructible, `GovernanceProposal`'s existing
`interval.evidence == STRONGER_EVIDENCE` gate already made a caller-authored
manifest insufficient to promote on its own; this repair makes that explicit in
the docstrings (`event_manifest`/`same_event_manifest` are descriptive record-
keeping fields, not an authority source) and adds regressions proving a 50-id
caller-authored manifest cannot produce a `PROMOTION_PROPOSAL`.

No new statistical method, capability framework, or future-authority stand-in
was added. `PROMOTION_PROPOSAL` and `compare_challenger()` remain exactly as
fail-closed as before this repair; what changed is that the *only* prior path
to bypass that closure -- direct object construction -- is now closed too.
Demotion and quarantine proposals are unaffected.

A future milestone must introduce, before promotion can exist: (a) a
prespecified, statistically valid inferential method that handles event
dependence and repeated looks, and (b) an authoritative evaluation-cohort/
manifest issuance boundary independent of caller input. Neither is implemented
here.

New regressions in `tests/test_m9e1_learning_governance_semantic_repair.py`:
the exact reviewer counterexample; a parametrized sweep of caller-supplied
`inferential_method` names (absent, plausible, empty) all failing closed on
`STRONGER_EVIDENCE`; `inferential_method` rejected even alongside a legitimate
`INCONCLUSIVE` evidence value; a 50-id caller-authored manifest failing to
produce a promotion proposal; `compare_challenger()` failing to return `True`
under fabricated stronger evidence; and 50 uniformly positive legitimate events
still yielding `INCONCLUSIVE` with no promotion authority.

## Status

- Semantic repair: **VERIFIED** by focused regressions and full-suite run.
- Authority-bypass repair: **VERIFIED** -- both independent-review
  counterexamples now fail closed; no constructible promotion-authority path
  exists in `services/learning/{evaluation,governance}.py`.
- Promotion evidence: **INCONCLUSIVE BY CONSTRUCTION** pending a prespecified
  inferential method for dependence and repeated looks, and an authoritative
  evaluation-cohort issuer. Neither is proposed here; establishing them is
  separate, unstarted work.
- Real settled learning evidence: **INSUFFICIENT REAL EVIDENCE** (unchanged).
- Production influence: **NONE**. Human acceptance: **PENDING**.
