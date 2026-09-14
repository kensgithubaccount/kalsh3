# M9-E1 — truthful event-level promotion authority

## Scope and baseline

Base: `ca72cb2b38b2ea11f4f3650a9ad31d9b5c348d2f`, after accepted GDP PR #157.
GDP integration remains closed and documentary feasibility is parked. This
repair changes only M9 evaluation/governance helpers, their tests and status docs.
It does not run a collector, revise a frozen evaluation, expose blind outcomes,
change production eligibility, place orders or deploy capital.

The independent AI acceptance reviewer reproduced five baseline defects: fifty
copies of one event produced stronger evidence; fifty negative contributions did
the same; `minimum=2` lowered the floor; a caller-created interval with count one
and arbitrary proposal count/manifest was accepted; and a one-event challenger
comparison returned true from a stronger-evidence flag. The actual comparison
symbol is `compare_challenger`, not the assessment's `challenger_wins` shorthand.

## Repair and compatibility

- `PairedEventSensitivity` validates nonempty unique event IDs, finite Decimal
  Brier scores in [0,1], integer timestamps and an integer minimum of at least 50.
  Identical and conflicting duplicate IDs both fail; event aggregation belongs
  upstream. This cannot establish that distinct caller IDs are independent or
  that the outcomes are authoritatively settled.
- The sorted event records produce the count, IDs and a SHA-256 manifest of a
  versioned deterministic JSON representation, including both directional scores
  and all timestamps. Decimal representations are retained exactly. Reordering
  records leaves the diagnostic unchanged; changing a bound field changes the
  manifest. The manifest identifies content, not statistical validity.
- Point estimate and leave-one-out extrema are descriptive sensitivity only.
  A single event has no leave-one-out range. Evidence is always INCONCLUSIVE;
  distinct counts below the chosen minimum, nonpositive observed contribution,
  and the absence of an accepted inference method have explicit reason labels.
- `paired_event_interval` remains callable but explicitly returns the new
  sensitivity type; callers must use `sensitivity_lower`/`sensitivity_upper`.
  Repository callers are migrated. No confidence-level claim is made.
- The frozen five-field `PerformanceInterval` legacy type and existing records
  are unchanged. Historical STRONGER_EVIDENCE labels are not rewritten and grant
  no promotion authority. Both promotion consumers reject such authority.
- Proposal metadata must agree with new diagnostics' derived count/manifest.
  Promotion proposals always fail closed, including positive samples above 50,
  pending an accepted prespecified inference method. Otherwise valid challenger
  comparisons return false; mismatched or duplicate cohorts fail. Demotion and
  quarantine proposals remain available with zero production influence.

This repair does not invent a dependence/repeated-look method. A new inference
method would need its own bounded design, predeclared evaluation/selection
protocol and acceptance before any eligibility can be emitted. Existing
ablation effective-sample fields remain legacy descriptive counts, not proof of
mathematical independence. The separate BH utility is not a promotion protocol.

## Acceptance and validation

Acceptance is limited to closing the five reproducible semantic failures and
correcting the statistical claim. Tests cover duplicates/conflicts, invalid
scores, minimum bypass, positive/negative/zero samples, exact record binding,
metadata/cohort disagreement, legacy flag rejection and immutable records.
Existing replay/configuration/rollback and learning isolation tests remain.

The separate read-only AI reviewer returned **PASS**, with no in-scope blockers,
after reviewing the patch and independently running all 48 focused tests using
its own temporary test directory. The implementer's focused run also passed 48.
Strict mypy passed all 294 source files; Ruff check/format passed; Bandit's
high-severity gate passed with zero high-severity findings. Full-suite and CI
integration verification are tracked in the PR and are not implied by this pass.

Reviewed source/test Git blobs (unchanged by review):

| File | Git blob |
| --- | --- |
| `services/learning/evaluation.py` | `eba07fe1dbef492fd9ff0b34df1119416cd994db` |
| `services/learning/governance.py` | `30231fe8baff8a92f7aa840bfb3a90607fc96cda` |
| `tests/test_learning_complete.py` | `f68fbff37c0a4b6271d3caf71af90dba17e33334` |
| `tests/test_m9_event_promotion_authority.py` | `be14afaff3feba090f009292ef17596d9a1d7d8a` |

## Completion rule and next work

One consolidated independent review should decide this exact repair against the
acceptance above. A blocker must include a reproducer, violated requirement and
effect within scope; optional improvements do not restart acceptance. Carry
forward a pass unless changed code or concrete regression contradicts it.

M9-E1 closure means the known false promotion claims are removed, not that M9
statistical eligibility or the entire project is complete. Oversight should
select the next executable item from current MASTER_SPEC/status evidence:
prespecify an inference protocol without outcome peeking, or complete a remaining
bounded runtime/demo acceptance under its existing authorization. Keep external
fee gaps and the weather blind period as explicit waiting dependencies. Report
engineering, empirical validation, runtime acceptance and production eligibility
separately; lack of profit evidence cannot be fixed by generic review cycles.
