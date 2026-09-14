# GDP evaluation-plan repair and integration closure

## Scope and evidence

Reviewed integration baseline: `d0fde5b6f7db4fdf06d35d0bede81d71b1757daf`
on `gdp-authority-integration-preflight`.

The user's supplied independent review reports the previous storage identity,
GDP bootstrap, and ledger/archive pairing blockers closed. This document does
not claim access to the full oversight transcript or replace that independent
review. The uploaded transcript contained only the exchange about exporting it.

The baseline has 15 integration commits after canonical main but no integration
PR was visible when this repair began. The workflow runs on main pushes and pull
requests, so pushing this integration branch alone does not trigger its CI.

## Reproduced defects and repair

1. Replacing `trial_ledger.EvaluationPlan` before GDP import lets
   `register_gdp_attempt()` append a fake evaluation identity with genuine ledger
   authentication. The same attack succeeds after GDP import. The original type
   guard and replay constructor both resolve the replaceable public export.
2. A genuine plan is rejected after the public export changes; replay also uses
   the replacement constructor. Pinning only the registration check would leave
   this second call site inconsistent.

The repair captures `_TRUSTED_EVALUATION_PLAN_TYPE` immediately after the class
definition and uses it for both the exact-type check and payload reconstruction.
This follows the repair class requested by the independent reviewer. It does not
change ledger schema, key handling, journal bytes, trial identities, or the GDP
source/decision policy. It prevents new poisoning; it does not rewrite an already
corrupt journal.

Three new regressions fail on the unmodified baseline and pass after repair:

- public-export replacement before GDP bootstrap;
- public-export replacement after GDP bootstrap;
- registration and replay of genuine plans while the public export is replaced.

The first two use separate copied packages and fresh processes. They require
rejection before any durable file changes, then legitimate registration and a
clean-process reopen of the same ledger. Real sockets are blocked. No canonical
operational storage is used.

## Baseline test-environment defect

The public composition endpoint-coverage test also failed on the untouched
baseline in this environment. Inherited proxy variables made urllib call
`set_tunnel` on the recorded HTTPS test transport before its fee request. The
public code caught that exception and correctly returned incomplete, but the
test had not reached all required sources.

Only the recorded-transport subprocess environment now excludes proxy variables.
A hostile-proxy case checks that source coverage, durable replay, and rejection
of a duplicate before acquisition still work. The socket guard, real parsers,
authority code, and endpoint assertions remain intact. Production proxy behavior
and all three accepted authority implementations are unchanged.

## Verification

- Focused identity, ledger, public composition, forgery, and settlement suite:
  **124 passed**.
- Repository Ruff lint and format: **passed**.
- Strict mypy: **passed**, 294 source files.
- Bandit high-severity gate: **passed**.
- Changed-file secret scan: **no findings**.
- Full pytest: **4,052 passed, 5 failed, 4 skipped** in 247.03 seconds.
  All five failures are the `real_checkout_identity` cases in
  `test_a05_s2b_activation_launcher.py`: the test expects `/usr/bin/git`, whereas
  this environment resolves `/usr/local/bin/git`. The same five cases were
  reproduced on untouched baseline `d0fde5b`; no launcher code was changed.
  Skips: three PostgreSQL cases without `KALSH3_TEST_POSTGRES_DSN`, and one CPI
  case without empirical P5A files. These counts supersede the supplied review's
  counts for this environment only.
- Exact-head CI and independent delta review: **pending**. Automatic approval
  review blocked the GitHub push because it requires explicit publication
  authorization for `kensgithubaccount/kalsh3`. No PR was created by this repair.

## What closes this engineering checkpoint

The accepted observable result is a durable, research-only
`EVIDENCE_INCOMPLETE` receipt, clean-process replay of the same receipt, and
duplicate rejection before new acquisition. It is not a positive trading signal.

For the final delta review, verify the baseline-to-head repair, the new attack
regressions, and the retained public-composition tests. Carry forward the prior
review's closed blockers unless a reproducible regression reopens one. Every
additional blocker needs an exact head, executable counterexample, violated
existing requirement, and bounded acceptance test. Report proposals outside the
agreed checkpoint separately; do not silently expand its threat model. A genuine
in-scope data-integrity failure must still block closure.

Green CI alone is not independent approval. After an independent delta PASS and
successful CI for the unchanged head, perform the existing merge/post-merge gate
and close this integration checkpoint. Do not restart the three adapter reviews
merely because the combined branch acquired a new commit.

## Next milestone: GDP evidence feasibility disposition

The fee resolver explicitly cannot establish market/event override coverage,
effective-time applicability, and precision/rounding. More object-identity repairs
cannot establish those external facts. Do not change COMPLETE issuance just to
obtain a positive acceptance result.

The next bounded deliverable is one evidence-feasibility table: each missing fact,
its exact first-party source/field or document clause, the scope it proves, the
unresolved gap, and an IMPLEMENTABLE or PARKED disposition. Preserve the exact
observation quarter, publication time, cutoff, settlement source, and revision
policy from contract rules; a ticker/date is not authority. Distinguish synthetic
test fixtures from preserved source evidence. Existing public-read permissions
must govern any new acquisitions; this repair starts none.

If the fee facts and selected contract semantics are provable, separately scope
the smallest adapter change and a full source-to-decision-to-outcome acceptance
test. If they are unprovable under the permitted sources and scope, park this GDP
experiment with explicit reopening conditions. Do not generate a new generic
security-repair cycle to compensate for unavailable evidence.

No live acquisition, scientific clock, account API, order, or capital action was
performed by this repair. Profitability and trading readiness remain unestablished.
