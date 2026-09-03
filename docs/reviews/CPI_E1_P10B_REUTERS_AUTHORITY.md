# CPI-E1-P10B -- Reuters consensus vintage authority: durable evidence handoff

## Result

This branch packages a durable, independently reviewable evidence bundle for
the three Reuters economist-consensus observations already established across
the CPI-E1-P10B.1 through P10B.4R1 review chain, plus the two Reuters UNKNOWN
sample events, so the review no longer depends on an ephemeral
`/private/tmp` scratchpad. It performs no acquisition beyond these five
already-reviewed events, no model fit or score, and no market comparison.

Canonical base for this branch: `origin/main` at
`d8fd8e2d860f6f602564bf60ce7370a0b694d947`
(tree `1711ebb9cb10fbb70ed41679038edef3c03827a4`), built in an isolated
worktree, not the shared checkout. This branch does not touch, supersede, or
merge PR #119.

## Reuters coverage sample (5 events)

| Event | Reference month | Cutoff (UTC) | Reuters value | Vintage status |
|---|---|---|---|---|
| KXCPI-25JUL | 2025-07 | 2025-08-12T12:29:00Z | Decimal("0.2") | **PASS** (anchor) |
| KXCPI-26JAN | 2026-01 | 2026-02-13T13:29:00Z | Decimal("0.3") | **PASS** |
| CPI-21SEP | 2021-09 | 2021-10-12T23:00:00Z | -- | UNKNOWN |
| CPI-23JUN | 2023-06 | 2023-07-12T12:25:00Z | -- | UNKNOWN |
| KXCPI-25DEC | 2025-12 | 2026-01-13T13:29:00Z | Decimal("0.3") | **PASS** |

**Total PASS: 3/5. Newly tested PASS: 2/4** (KXCPI-26JAN and KXCPI-25DEC, the
two rows repaired from P10B.4's initial UNKNOWN in P10B.4R1). Gate threshold
(>=3/5 total, >=2/4 newly tested) is met.

`CPI-21SEP` and `CPI-23JUN` remain UNKNOWN, not silently upgraded:

- **CPI-21SEP**: no Reuters-attributed, pre-cutoff wire artifact was located
  after extensive search across two prior review passes. Only FXStreet
  multi-bank roundups and post-release actuals were found.
- **CPI-23JUN**: the best candidate located ("US consumer inflation likely
  increased at a slow pace in June as gasoline prices retreated") was
  independently fetched and found to be **the wrong year** -- a
  same-template Reuters wire piece for the June **2026** cycle
  (`datePublished` 2026-07-14), confirmed by direct metadata extraction
  rather than trusting a search-engine summary. No correctly-dated Reuters
  artifact was located for June 2023.

## Cleveland claims, kept separate

This bundle carries only Reuters observations. The pre-existing Cleveland Fed
observations for the two overlapping events are recorded in
`coverage.json` as distinct claims and are not merged with the Reuters
values above, even where both happen to be PASS for the same event:

- KXCPI-26JAN: Cleveland `Decimal("0.13")`, PASS -- separate predictor
  family from this bundle's Reuters `Decimal("0.3")` PASS for the same
  event.
- KXCPI-25JUL: Cleveland UNKNOWN -- separate from this bundle's Reuters
  `Decimal("0.2")` PASS for the same event.

## Timestamp handling notes

- **KXCPI-25DEC**: two independently operated hosts (Yahoo, a distinct
  company, and WMBD, an unrelated Peoria IL radio station) agree to the
  exact second on `2026-01-13T05:03:53Z`; this is the governing value.
  TradingView's distinct `2026-01-13T05:00:01Z` timestamp is preserved as a
  separate, disclosed field rather than discarded or silently reconciled.
  WMBD's `dateModified` (`2026-01-13T10:25:30Z`) was investigated: the
  load-bearing forecast text is byte-identical across all three hosts
  including the modified copy, and the modification occurred ~3 hours
  before both the actual BLS release and the Kalshi cutoff, so it cannot be
  a post-release edit. The shutdown/imputation methodology caveat ("The BLS
  estimated the CPI rose 0.2% from September to November... carry-forward
  imputation method treated October prices as unchanged") is preserved in
  `extract.json`, not dropped.
- **KXCPI-26JAN**: the two recoverable syndication hosts disagree by 12m30s
  (`05:00:01Z` TradingView vs. `05:12:31Z` Yahoo). This range is preserved
  explicitly as `observed_publication_range` rather than collapsed into a
  falsely exact instant. The conservative (later) bound,
  `2026-02-13T05:12:31Z`, is used for the admissibility determination,
  yielding a **minimum proven lead time of 8h16m29s** -- still comfortably
  clear of the 13:29:00Z cutoff. The AOL URL supplied for this event 404s
  and was not used.

## Durable raw-response store: not available

This repository's existing "evidence store" pattern (see
`docs/PERPS_SHADOW_RESEARCH.md` and related M25B7/M27B2/M26B reviews) is an
internal append-only SQLite store for this system's own production/market
evidence -- it is not an archive suited to third-party copyrighted news
content, and no other durable, independently accessible store (blob bucket,
LFS remote, artifact host) is available to this working environment.

Consistent with the instruction not to redistribute complete copyrighted
Reuters wire text without confirmed permission, and not to substitute
`/private/tmp` for a durable store, **complete raw HTTP responses are not
committed to this repository.** Only minimal, reviewable artifacts are
committed: metadata, URLs, byte counts and hashes as fetched, hashed
load-bearing excerpts, normalized target/value fields, timing evidence,
provenance, and modification assessments. Every fetch's raw-page hash is
disclosed with an explicit note that it is not expected to be independently
reproducible (these are dynamically rendered pages), while every
load-bearing-sentence hash **was** independently reproduced against a
second, later fetch of the same public URLs performed in the same task run
that produced this bundle -- demonstrating the substantive claims are
stable and reviewer-checkable, not one-off or fabricated.

Full detail, including the explicit sufficiency assessment and the residual
risk if all syndication hosts for an event later go dark, is in
`docs/reviews/artifacts/cpi-p10b-reuters/manifest.json` under
`durable_raw_store`.

## Validation

`scripts/validate_cpi_p10b_reuters_authority.py` is a read-only, offline
validator (no network access) that recomputes every committed artifact's
hash against disk, recomputes the coverage arithmetic and gate result, and
checks each PASS receipt's cutoff ordering, exact Decimal precision,
prospective-language flag, and syndication-host count. It is exercised by
`tests/test_cpi_p10b_reuters_authority.py`.

## Not established

This bundle does not establish full 42-event P10A coverage, provider-wide
archival integrity, predictive edge, market-relative comparison, PnL, or
profitability. No model was fit or scored. No P8/P9A/P9B/P10A frozen
evidence was read, recollected, or modified by this branch.
