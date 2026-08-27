# KU-A3.1 Researchability Hard Gates

## Scope and authority

KU-A3.1 is a fail-closed, research-only hard-gate layer over the exact canonical KU-A2.2
research-family result. It does not reclassify markets or invent families. Each family actually
represented by A2.2 receives one deterministic, content-addressed receipt bound to the exact
A2.2 result/report/mapping identities and, through them, the exact A2.1 result/manifest and
KU-A1 census, coverage, and capture identities.

Every receipt has `research_only == True` and `production_influence == 0`. There is no
readiness score, weighted score, family rank, best-family selection, profitability/EV claim,
capital recommendation, trade eligibility, lifecycle promotion, or execution authority.
Canonical sorting is used only for deterministic identity; it is not a ranking between families.

## Gate semantics

Each receipt contains exactly seven decisions, each with one of `PASS`, `BLOCKED`, or
`UNKNOWN` plus deterministic reason codes and evidence provenance:

- **G1 — SETTLEMENT PROOF:** whether canonical semantic and settlement-source evidence proves
  the exact contractual settlement target. Titles, categories, keywords, M27B routes, family
  labels, and inferred intent cannot prove it.
- **G2 — PERMITTED SOURCE:** whether explicit source-governance evidence proves a permitted
  source path for that target. Hostname or apparent authority cannot grant permission.
- **G3 — HISTORICAL TRUTH:** whether admissible historical ground truth exists for the exact
  target. Current data and current endpoints cannot prove historical availability.
- **G4 — POINT-IN-TIME RECONSTRUCTION:** whether historical decision-time information can be
  reconstructed without future leakage. Final/revised data and current snapshots are not PIT
  proof.
- **G5 — EVIDENCE-UNIT POLICY:** whether a canonical family-specific admissible atomic evidence
  unit/policy is explicitly defined rather than inferred.
- **G6 — ECONOMICS OBSERVABILITY:** whether future research could reconstruct the required
  market-side economics inputs under canonical interfaces. This gate never computes EV, edge,
  profitability, or duplicates M28D economics logic.
- **G7 — AUTHORITY ISOLATION:** whether research remains isolated from credentials, account
  reads, signer/risk/order authority, arm/burn/final acknowledgement, mutation, and execution.

`PASS` requires positive canonical proof. `BLOCKED` requires positive canonical evidence of a
known blocker. `UNKNOWN` means the repository lacks admissible evidence proving either state;
absence of evidence never becomes `PASS`. No aggregate "researchable" state is emitted.

## Structural versus empirical posture

At this checkpoint, G1-G6 are deliberately `UNKNOWN` for every represented A2.2 family. The
A2.2 mapping proves structural family identity only and expressly does not prove researchability.
A2.1 settlement-source projection likewise does not confer source permission. The repository
contains no additional A3.1 canonical artifacts that positively prove G1, G2, or G5.

G3, G4, and the empirical-observability component needed by G6 additionally carry
`EMPIRICAL_ARTIFACT_UNAVAILABLE`. KU-A2.2 established that no committed whole-exchange KU-A2.1
result artifact exists and that no public/live acquisition was performed. Test fixtures prove
code behavior only and can never convert these empirical gates to `PASS`.

G7 is the sole structural `PASS`: the exact A1 -> A2.1 -> A2.2 chain is research-only with zero
production influence, and the A3.1 implementation has no network acquisition, authenticated
account, credential, signer, risk, execution, order, mutation, arm, burn, or final-acknowledgement
dependency.

`UNKNOWN_UNMAPPED` is retained exactly when A2.2 emits it. It receives the same seven-gate
receipt, with G1-G6 remaining `UNKNOWN` and explicit `UNKNOWN_UNMAPPED` provenance; G7 remains
an authority-isolation statement only and does not make the family researchable.

## Next dependency

KU-A3.2 may add separately canonicalized admissible evidence needed to resolve currently
unknown gates. It must preserve the A3.1 fail-closed state model and authority boundary rather
than backfilling fixture, live, inferred, hostname, title/category, or family-label evidence as
proof.
