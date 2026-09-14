# GDP evaluation-plan repair and integration closure

## Canonical closure record — September 14, 2026

PR #157 is merged as `524d04092cb23364073db21185ffa4dc91cc35b8`.
Its parents are prior main `636e411b155b826b1ce17723853b34766311269d`
and independently reviewed head `5c8dacc4236338dd0ae4756b2d72f85dc9193e69`.
The merge tree is exactly the reviewed tree:
`d06ca6df4dcec1f859639a6544d164f2ebf77905`.

The user supplied the separate reviewer's PASS on the reported alias finding.
The reviewer confirmed fresh-fetch identity, rejection of the prior alias and
public-name cases, unchanged durable state on rejection, and no residual issue
from that finding. Their isolated suite reported 4,065 passed, one environmental
skip, and no failures. This is an attributed independent report, not an
implementer-authored approval or a GitHub review submission.

[Reviewed-head CI run 34856612759](https://github.com/kensgithubaccount/kalsh3/actions/runs/34856612759)
passed all four jobs, with 4,064 passed and four skipped in verify; all ten
identity cases passed. Ruff/format and strict mypy across 294 source files passed.
The environment-specific reviewer and CI counts are recorded separately.

[Post-merge CI run 34866085527](https://github.com/kensgithubaccount/kalsh3/actions/runs/34866085527)
is bound to the merge commit. Consult its final conclusion and the PR's closure
record for the post-merge gate. Later documentation-only commits do not alter
the accepted source/test tree. They are not a reason to reopen this finding.

The next active milestone is
[GDP evidence feasibility](GDP_EVIDENCE_FEASIBILITY_BRIEF.md).
The sections below retain the repair history. A historical pending status
must not restart a closed review.


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

## Initial public-name repair (superseded)

1. Replacing `trial_ledger.EvaluationPlan` before GDP import lets
   `register_gdp_attempt()` append a fake evaluation identity with genuine ledger
   authentication. The same attack succeeds after GDP import. The original type
   guard and replay constructor both resolve the replaceable public export.
2. A genuine plan is rejected after the public export changes; replay also uses
   the replacement constructor. Pinning only the registration check would leave
   this second call site inconsistent.

The initial repair captured a module-level `_TRUSTED_EVALUATION_PLAN_TYPE`
alias and used it for the exact-type check and reconstruction. Independent
review of published head `a04457a0e3ab31afa2ecbcb25193359f6bc32536` correctly
rejected this repair: the alias itself remained rebindable. The earlier claim
that this provided permanent type capture was too strong.

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

## Historical verification of the initial repair

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
- After explicit publication authorization, draft PR #157 was created.
  CI run 34815849182 on `a04457a0e3ab31afa2ecbcb25193359f6bc32536`
  passed all four jobs; its verify job reported 4,057 passed and 4 skipped.
- The subsequent user-supplied independent review reported 4,058 passed,
  1 environmental skip, and no failures in its own environment, but returned
  **REPAIR REQUIRED** for the mutable private alias. Test counts are
  environment-specific and do not override that finding.

## Closure repair following independent review

Repair baseline: `a04457a0e3ab31afa2ecbcb25193359f6bc32536`.

The reviewer demonstrated that assigning a forged constructor to
`trial_ledger._TRUSTED_EVALUATION_PLAN_TYPE` allowed registration to append a
noncanonical evaluation identity and replay to accept the same false identity.
This is an in-scope ledger-integrity defect. No fabricated positive trading
receipt was demonstrated or is claimed here.

Registration and payload reconstruction now close over the same original
`EvaluationPlan` type when `trial_ledger` finishes importing. The module alias
is removed. The bootstrap installs both implementations once and is then deleted.
The public method signature stays intact; its definition-time placeholder fails
closed. There is no retained unvalidated registration implementation.
Rebinding either old type name, or both names, cannot change the captured type.

The registration and reconstruction bodies retain their existing validation and
serialization logic. The patch moves them into the bootstrap closure; it does
not change the ledger schema, journal format, financial policy, or source
authorities. It prevents the reported new invalid registration; it does not
repair an already-corrupted journal.

Regression coverage now includes ten identity cases:
- The two existing GDP public-name cases before and after GDP bootstrap.
- Four direct-ledger cases replacing the private alias, or both names, before
  and after GDP bootstrap. They require byte-identical durable files on rejection,
  then genuine registration while the alias is still replaced, and a second
  clean-process reopen of the same copied-package canonical ledger.
- Three genuine-plan registration/reopen cases under public, private, and
  simultaneous name replacement.
- One reconstructed-payload case where the alias supplies the same false identity
  as the payload. Reconstruction must reject the identity mismatch even though
  the payload content hash has been recalculated. This is a reconstruction unit
  test, not a claim of forged journal authentication.

The seven additional cases target the reviewer's demonstrated gap. The original
public composition tests, including transport guards and proxy cases, remain
unchanged. Only isolated temporary storage is used by the new regression cases.

This follow-up session has no local Python execution tool. The prior-head
reproduction is attributed to the supplied independent review. New execution
results must come from CI on the published follow-up head; the PR description
records that head, run, and outcome. The independent follow-up PASS is now
recorded in the canonical closure record above.

Review scope is ordinary module-attribute replacement of these type references,
including pre-GDP import order. This does not claim a sandbox against arbitrary
Python code-object, class-method, or closure-cell modification. New concerns
must be assessed against an existing requirement and a concrete failed behavior.

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
