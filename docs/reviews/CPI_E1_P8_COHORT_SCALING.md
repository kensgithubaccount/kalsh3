# CPI-E1-P8 — Historical Cohort Scaling / Source Audit

## Verdict

**CPI-E1-P8 COHORT SCALING BLOCKED**

This checkpoint does not authorize a cohort expansion. The BLS annual ZIP
shortcut remains unproven, and no additional release event is admitted to the
authoritative cohort.

`research_only = true`

`production_influence = 0`

No forecast-model fitting, profitability claim, execution authority, or risk
authority is introduced.

## Exact-head and branch provenance

- Repository: `kensgithubaccount/kalsh3`
- Requested canonical base: `7aa43ea605fb44bc7db2572385bc61382ad5d5e5`
- Requested canonical tree: `f1d978765a3cd7be759987087f748f047db051fb`
- Checked-out tree: `f1d978765a3cd7be759987087f748f047db051fb` (exact match)
- Fresh branch: `cpi-e1-p8-cohort-scaling`

The requested base commit object was not present locally. An attempted fetch
of that exact object could not reach the remote because GitHub DNS/network
access was unavailable. The branch was therefore created from the checked-out
commit whose tree exactly matches the requested canonical tree; this limitation
must be resolved by an independent reviewer before merge.

`docs/IMPLEMENTATION_STATUS.md` was not modified.

## Phase 1 — annual ZIP shortcut

The reviewed official locator is:

`https://www.bls.gov/cpi/tables/supplemental-files/archive-2024.zip`

The official index was readable at:

`https://www.bls.gov/cpi/tables/supplemental-files/home.htm`

The index states that previous-year supplemental files are supplied as one
compressed file per year and explicitly cautions that archived CPI
supplemental data may have been revised in subsequent editions. It specifically
states that seasonally adjusted indexes and percent changes are revised with
the January release for the preceding five years.

The ZIP response itself could not be downloaded by the available browser
retrieval path because its content type is `application/x-zip-compressed`.
The local checkout contains no annual ZIP, XLSX member, or other 2024 archive
copy. Consequently the following required assertions are **not proven**:

- exact ZIP member enumeration and member paths/names;
- ZIP SHA-256 and every member SHA-256;
- release/reference-month binding for every News Release Table 1 member;
- whether members are monthly editions or later revised replacements;
- headline CPI-U all-items SA MoM comparison against exact archived HTML/PDF;
- January seasonal-revision behavior;
- exact release-vintage identity for any member.

The BLS warning is itself sufficient to prevent treating an uninspected annual
archive as initial-release truth. No current BLS database value, Kalshi
`expiration_value`, mirror, Internet Archive copy, or later revised seasonal
adjusted value was used as a substitute.

## Required browser-download artifact

Please supply one untouched browser download of the exact annual ZIP:

```text
archive-2024.zip
URL: https://www.bls.gov/cpi/tables/supplemental-files/archive-2024.zip
```

The file must be the raw downloaded ZIP, without extraction/recompression or
editing. On receipt, the audit must record the ZIP byte count and SHA-256,
enumerate every member in archive order, hash every member's raw bytes, retain
each exact member path/name, and compare each Table 1 headline value with the
corresponding exact BLS archived release HTML/PDF. January must be tested as a
seasonal-revision boundary. A supplied ZIP alone does not establish vintage;
the member-to-release comparisons must pass independently.

If the annual ZIP fails any of those checks, the next-cheapest safe official
path is one human-browser-attested exact archived BLS HTML release per event,
using the existing P5A manual lane. That path preserves P1–P7 provenance but
does not permit an annual ZIP to authorize multiple releases.

## Phase 2 — authority design decision

No P8 import path was implemented. An annual ZIP may only become a reviewed
multi-event source if every internal member is independently hashed,
unambiguously release-bound, and proven to be release-vintage by the required
HTML/PDF comparison. The available evidence does not satisfy those conditions.

The existing P5A/P6 lane remains the only admissible BLS initial-release path.
It remains human-attested, research-only, and distinct from P4 automated HTTP
provenance.

## Phase 3 — maximum proven cohort

The maximum authoritative cohort remains **3 independent release events**:

| Release event | Initial-release evidence | P7-compatible settlement evidence |
|---|---|---|
| July 2025 | exact P5A/P6 BLS HTML fixture | exact finalized KXCPI July evidence |
| December 2025 | exact P5A/P6 BLS HTML fixture | exact finalized KXCPI December evidence |
| January 2026 | exact P5A/P6 BLS HTML fixture | exact finalized KXCPI January evidence |

Sibling threshold markets are not counted separately. No new event is counted
because no additional exact BLS initial-release artifact and matching P7
settlement evidence were proven in this checkout.

## Scope and safety review

This P8 branch adds only this independent review receipt. It does not modify
shared source-policy, acquisition, value-parser, settlement, status, model,
execution, risk, or credential files. Existing P1–P7 controls remain the
authority boundary. No real-money order or production write was performed.
