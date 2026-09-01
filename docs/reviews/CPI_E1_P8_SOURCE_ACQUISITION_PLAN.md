# CPI-E1-P8 — Source Acquisition Optimization Plan

## Decision

**CPI-E1-P8 SOURCE ACQUISITION PLAN READY — WAITING FOR TEST ARTIFACT**

This is a planning receipt only. It does not implement an importer, alter CPI
truth semantics, promote the annual ZIP shortcut, fit a model, or make a
profitability claim. Existing P1–P7 authority boundaries remain unchanged.

`research_only = true`

`production_influence = 0`

## Cohort definition and chronology rule

C independently reports 60 independent KXCPI release events spanning
`2021-06` through `2026-05`, with approximately 474 sibling markets. This
receipt treats that month range as the requested event manifest. Sibling
threshold markets remain one underlying release event.

Release dates below come from BLS release chronology, not Kalshi close or
settlement timestamps. BLS's normal documented publication time is `08:30 ET`.
The exact archived release identifier is the date-coded BLS archive locator;
the HTML form is preferred because it is the artifact accepted by P1/P5A/P6.
An annual supplemental member is only a potential candidate until its vintage
is independently proven.

## Exact 60-event acquisition manifest

Status meanings:

- `ALREADY_PROVEN`: exact P5A/P6 BLS artifact and P7-compatible Kalshi evidence
  already exist in this repository.
- `NEEDS_BROWSER_ARTIFACT`: exact official release is identified but no exact
  local artifact is yet bound.
- `REVISION_RISK`: the release is January/currently exposed to BLS seasonal
  adjustment revision behavior; it requires the same exact-vintage checks.
- `UNKNOWN`: BLS did not publish a corresponding release, or chronology/source
  identity cannot be established from the available official record.
- `SAFE_BUNDLE_CANDIDATE`: deliberately unused before the annual ZIP test.

| # | Reference month | BLS release date | Expected time | Exact archived identifier | Preferred artifact / official locator | Vintage / January risk | Status |
|---:|---|---|---|---|---|---|---|
| 1 | 2021-06 | 2021-07-13 | 08:30 ET | `cpi_07132021` | HTML `https://www.bls.gov/news.release/archives/cpi_07132021.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 2 | 2021-07 | 2021-08-11 | 08:30 ET | `cpi_08112021` | HTML `https://www.bls.gov/news.release/archives/cpi_08112021.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 3 | 2021-08 | 2021-09-14 | 08:30 ET | `cpi_09142021` | HTML `https://www.bls.gov/news.release/archives/cpi_09142021.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 4 | 2021-09 | 2021-10-13 | 08:30 ET | `cpi_10132021` | HTML `https://www.bls.gov/news.release/archives/cpi_10132021.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 5 | 2021-10 | 2021-11-10 | 08:30 ET | `cpi_11102021` | HTML `https://www.bls.gov/news.release/archives/cpi_11102021.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 6 | 2021-11 | 2021-12-10 | 08:30 ET | `cpi_12102021` | HTML `https://www.bls.gov/news.release/archives/cpi_12102021.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 7 | 2021-12 | 2022-01-12 | 08:30 ET | `cpi_01122022` | HTML `https://www.bls.gov/news.release/archives/cpi_01122022.htm` | Initial current month; January seasonal revision boundary | REVISION_RISK |
| 8 | 2022-01 | 2022-02-10 | 08:30 ET | `cpi_02102022` | HTML `https://www.bls.gov/news.release/archives/cpi_02102022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 9 | 2022-02 | 2022-03-10 | 08:30 ET | `cpi_03102022` | HTML `https://www.bls.gov/news.release/archives/cpi_03102022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 10 | 2022-03 | 2022-04-12 | 08:30 ET | `cpi_04122022` | HTML `https://www.bls.gov/news.release/archives/cpi_04122022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 11 | 2022-04 | 2022-05-11 | 08:30 ET | `cpi_05112022` | HTML `https://www.bls.gov/news.release/archives/cpi_05112022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 12 | 2022-05 | 2022-06-10 | 08:30 ET | `cpi_06102022` | HTML `https://www.bls.gov/news.release/archives/cpi_06102022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 13 | 2022-06 | 2022-07-13 | 08:30 ET | `cpi_07132022` | HTML `https://www.bls.gov/news.release/archives/cpi_07132022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 14 | 2022-07 | 2022-08-10 | 08:30 ET | `cpi_08102022` | HTML `https://www.bls.gov/news.release/archives/cpi_08102022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 15 | 2022-08 | 2022-09-13 | 08:30 ET | `cpi_09132022` | HTML `https://www.bls.gov/news.release/archives/cpi_09132022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 16 | 2022-09 | 2022-10-13 | 08:30 ET | `cpi_10132022` | HTML `https://www.bls.gov/news.release/archives/cpi_10132022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 17 | 2022-10 | 2022-11-10 | 08:30 ET | `cpi_11102022` | HTML `https://www.bls.gov/news.release/archives/cpi_11102022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 18 | 2022-11 | 2022-12-13 | 08:30 ET | `cpi_12132022` | HTML `https://www.bls.gov/news.release/archives/cpi_12132022.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 19 | 2022-12 | 2023-01-12 | 08:30 ET | `cpi_01122023` | HTML `https://www.bls.gov/news.release/archives/cpi_01122023.htm` | Initial current month; January seasonal revision boundary | REVISION_RISK |
| 20 | 2023-01 | 2023-02-14 | 08:30 ET | `cpi_02142023` | HTML `https://www.bls.gov/news.release/archives/cpi_02142023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 21 | 2023-02 | 2023-03-14 | 08:30 ET | `cpi_03142023` | HTML `https://www.bls.gov/news.release/archives/cpi_03142023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 22 | 2023-03 | 2023-04-12 | 08:30 ET | `cpi_04122023` | HTML `https://www.bls.gov/news.release/archives/cpi_04122023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 23 | 2023-04 | 2023-05-10 | 08:30 ET | `cpi_05102023` | HTML `https://www.bls.gov/news.release/archives/cpi_05102023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 24 | 2023-05 | 2023-06-13 | 08:30 ET | `cpi_06132023` | HTML `https://www.bls.gov/news.release/archives/cpi_06132023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 25 | 2023-06 | 2023-07-12 | 08:30 ET | `cpi_07122023` | HTML `https://www.bls.gov/news.release/archives/cpi_07122023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 26 | 2023-07 | 2023-08-10 | 08:30 ET | `cpi_08102023` | HTML `https://www.bls.gov/news.release/archives/cpi_08102023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 27 | 2023-08 | 2023-09-13 | 08:30 ET | `cpi_09132023` | HTML `https://www.bls.gov/news.release/archives/cpi_09132023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 28 | 2023-09 | 2023-10-12 | 08:30 ET | `cpi_10122023` | HTML `https://www.bls.gov/news.release/archives/cpi_10122023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 29 | 2023-10 | 2023-11-14 | 08:30 ET | `cpi_11142023` | HTML `https://www.bls.gov/news.release/archives/cpi_11142023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 30 | 2023-11 | 2023-12-12 | 08:30 ET | `cpi_12122023` | HTML `https://www.bls.gov/news.release/archives/cpi_12122023.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 31 | 2023-12 | 2024-01-11 | 08:30 ET | `cpi_01112024` | HTML `https://www.bls.gov/news.release/archives/cpi_01112024.htm` | Initial current month; January seasonal revision boundary | REVISION_RISK |
| 32 | 2024-01 | 2024-02-13 | 08:30 ET | `cpi_02132024` | HTML `https://www.bls.gov/news.release/archives/cpi_02132024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 33 | 2024-02 | 2024-03-12 | 08:30 ET | `cpi_03122024` | HTML `https://www.bls.gov/news.release/archives/cpi_03122024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 34 | 2024-03 | 2024-04-10 | 08:30 ET | `cpi_04102024` | HTML `https://www.bls.gov/news.release/archives/cpi_04102024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 35 | 2024-04 | 2024-05-15 | 08:30 ET | `cpi_05152024` | HTML `https://www.bls.gov/news.release/archives/cpi_05152024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 36 | 2024-05 | 2024-06-12 | 08:30 ET | `cpi_06122024` | HTML `https://www.bls.gov/news.release/archives/cpi_06122024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 37 | 2024-06 | 2024-07-11 | 08:30 ET | `cpi_07112024` | HTML `https://www.bls.gov/news.release/archives/cpi_07112024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 38 | 2024-07 | 2024-08-14 | 08:30 ET | `cpi_08142024` | HTML `https://www.bls.gov/news.release/archives/cpi_08142024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 39 | 2024-08 | 2024-09-11 | 08:30 ET | `cpi_09112024` | HTML `https://www.bls.gov/news.release/archives/cpi_09112024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 40 | 2024-09 | 2024-10-10 | 08:30 ET | `cpi_10102024` | HTML `https://www.bls.gov/news.release/archives/cpi_10102024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 41 | 2024-10 | 2024-11-13 | 08:30 ET | `cpi_11132024` | HTML `https://www.bls.gov/news.release/archives/cpi_11132024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 42 | 2024-11 | 2024-12-11 | 08:30 ET | `cpi_12112024` | HTML `https://www.bls.gov/news.release/archives/cpi_12112024.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 43 | 2024-12 | 2025-01-15 | 08:30 ET | `cpi_01152025` | HTML `https://www.bls.gov/news.release/archives/cpi_01152025.htm` | Initial current month; January seasonal revision boundary | REVISION_RISK |
| 44 | 2025-01 | 2025-02-12 | 08:30 ET | `cpi_02122025` | HTML `https://www.bls.gov/news.release/archives/cpi_02122025.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 45 | 2025-02 | 2025-03-12 | 08:30 ET | `cpi_03122025` | HTML `https://www.bls.gov/news.release/archives/cpi_03122025.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 46 | 2025-03 | 2025-04-10 | 08:30 ET | `cpi_04102025` | HTML `https://www.bls.gov/news.release/archives/cpi_04102025.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 47 | 2025-04 | 2025-05-13 | 08:30 ET | `cpi_05132025` | HTML `https://www.bls.gov/news.release/archives/cpi_05132025.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 48 | 2025-05 | 2025-06-11 | 08:30 ET | `cpi_06112025` | HTML `https://www.bls.gov/news.release/archives/cpi_06112025.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 49 | 2025-06 | 2025-07-15 | 08:30 ET | `cpi_07152025` | HTML `https://www.bls.gov/news.release/archives/cpi_07152025.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 50 | 2025-07 | 2025-08-12 | 08:30 ET | `cpi_08122025` | HTML `https://www.bls.gov/news.release/archives/cpi_08122025.htm` | Exact P5A/P6 artifact; not January | ALREADY_PROVEN |
| 51 | 2025-08 | 2025-09-11 | 08:30 ET | `cpi_09112025` | HTML `https://www.bls.gov/news.release/archives/cpi_09112025.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 52 | 2025-09 | 2025-10-24 | 08:30 ET | `cpi_10242025` | HTML `https://www.bls.gov/news.release/archives/cpi_10242025.htm` | Delayed official release; not January | NEEDS_BROWSER_ARTIFACT |
| 53 | 2025-10 | — | — | **No BLS October 2025 CPI release** | No official release artifact; do not substitute November/December or database values | No initial-release truth exists for this reference month | UNKNOWN |
| 54 | 2025-11 | 2025-12-18 | 08:30 ET | `cpi_12182025` | HTML `https://www.bls.gov/news.release/archives/cpi_12182025.htm` | Official release covers a shutdown-affected period; not January | NEEDS_BROWSER_ARTIFACT |
| 55 | 2025-12 | 2026-01-13 | 08:30 ET | `cpi_01132026` | HTML `https://www.bls.gov/news.release/archives/cpi_01132026.htm` | Exact P5A/P6 artifact; January seasonal revision boundary | ALREADY_PROVEN |
| 56 | 2026-01 | 2026-02-13 | 08:30 ET | `cpi_02132026` | HTML `https://www.bls.gov/news.release/archives/cpi_02132026.htm` | Exact P5A/P6 artifact; not January release | ALREADY_PROVEN |
| 57 | 2026-02 | 2026-03-11 | 08:30 ET | `cpi_03112026` | HTML `https://www.bls.gov/news.release/archives/cpi_03112026.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 58 | 2026-03 | 2026-04-10 | 08:30 ET | `cpi_04102026` | HTML `https://www.bls.gov/news.release/archives/cpi_04102026.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 59 | 2026-04 | 2026-05-12 | 08:30 ET | `cpi_05122026` | HTML `https://www.bls.gov/news.release/archives/cpi_05122026.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |
| 60 | 2026-05 | 2026-06-10 | 08:30 ET | `cpi_06102026` | HTML `https://www.bls.gov/news.release/archives/cpi_06102026.htm` | HTML candidate; not January | NEEDS_BROWSER_ARTIFACT |

The BLS archive record confirms the special 2025 case: October 2025 CPI was
not published because of the lapse in appropriations, September 2025 was
released on October 24, and November 2025 was released on December 18. This
means the stated 60-event Kalshi cohort cannot automatically become a 60-event
BLS initial-release cohort.

## Phase 2 — minimum-download strategy

| Strategy | Provenance safety | Vintage fidelity | Human downloads | Parsing complexity | Revision contamination | Decision |
|---|---|---|---:|---|---|---|
| A. One annual ZIP per year | Unproven until ZIP/member audit | Potentially poor; BLS warns of later revisions | 4 for 2021–2024, if accepted | ZIP/XLSX/member binding | High around January | Test first; never authorize in advance |
| B. One archived HTML/PDF per release | Highest within P5A/P6 | Exact release-vintage candidate | 59 available BLS releases in this range | Existing canonical HTML parser | Lowest, if untouched release file | Safe fallback |
| C. Official yearly/monthly release bundles | Unreviewed as a vintage container | Unknown | Potentially fewer | Container-specific | Unknown/high until tested | Not admissible yet |
| D. BLS release-page monthly XLSX | Official, but archive members may be revised | Unknown until comparison | Up to one per month | XLSX parser not in P6 | Explicit BLS revision risk | Candidate only |

The minimum realistic strategy is conditional:

1. Test one browser-downloaded `archive-2024.zip` first. It could potentially
   cover 12 release events, but covers **zero authoritatively until it passes**
   the member/vintage test.
2. If 2024 passes, test the 2021, 2022, and 2023 annual ZIPs before using them.
   Those four ZIPs could potentially cover 43 events in the requested range
   (June 2021 through December 2024), subject to each member passing.
3. Use exact monthly HTML for the remaining 17 calendar months in 2025–2026,
   excluding October 2025, which has no BLS release. Three are already proven,
   so at most 14 additional monthly artifacts are needed for that tail.

Thus the first download is one artifact with a potential 12-event coverage,
not a 12-event authority grant.

## Download first

```text
DOWNLOAD:
- archive-2024.zip
  URL: https://www.bls.gov/cpi/tables/supplemental-files/archive-2024.zip
  Potential coverage: 12 reference-month events (2024-01 through 2024-12)
  Authorized coverage before testing: 0
```

Do not extract and recompress it. Supply the untouched browser download so the
raw ZIP hash and exact member bytes can be retained.

## Phase 3 — one-artifact ZIP test

When supplied, the first test will:

1. hash the raw ZIP and record byte count;
2. enumerate members in archive order, retaining exact path/name, size, and
   raw member SHA-256;
3. identify every monthly News Release Table 1 XLSX and extract embedded
   workbook metadata/date strings without treating metadata as proof by itself;
4. bind each member to its claimed reference month and expected release
   identifier;
5. compare the headline CPI-U U.S. city average all-items SA MoM value from
   at least one ordinary mid-year member, December, and January against the
   exact archived BLS HTML/PDF release;
6. specifically compare January's current value and the preceding historical
   SA values to detect seasonal-factor replacement;
7. reject the annual shortcut if any member is later-revised, ambiguous,
   duplicated, missing, misdated, or inconsistent with the exact release.

The test must not use current BLS database values, Kalshi expiration values,
mirrors, Internet Archive copies, or later revised seasonal-adjusted data as
initial-release truth. A passing 2024 sample would justify further annual ZIP
tests, not an automatic blanket authorization.

## Phase 4 — deferred bulk manual import design

No importer is implemented until the source strategy is proven. If monthly
browser artifacts remain necessary, the least-painful reviewed design is:

```text
DOWNLOAD OFFICIAL FILES -> DROP UNTOUCHED FILES IN -> IMPORTER HASHES AND BINDS
-> CANONICAL P6 PARSER -> DUPLICATE/CONFLICT CHECK -> ONE IMMUTABLE OBSERVATION
```

The future importer must derive release month and locator from the artifact
and reviewed filename/manifest mapping, never accept caller-typed CPI values,
reject non-BLS paths, preserve `MANUAL_BROWSER_ATTESTED`, reject duplicate or
conflicting release identities, and issue only canonical P6 observations.
It must not turn an annual ZIP into a multi-event authority without the
member-level vintage proof described above.

## Immediate blocker

The plan is ready, but the first test cannot run until the user supplies the
untouched browser download of `archive-2024.zip`. The repository currently has
no 60-event Kalshi evidence manifest and no annual ZIP artifact, so this plan
does not claim P7 settlement coverage beyond the three already proven events.
