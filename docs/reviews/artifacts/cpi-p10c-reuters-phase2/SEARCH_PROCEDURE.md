# CPI-E1-P10C Phase 2 — deterministic Reuters acquisition procedure v2

v1 (single-pass live web search, vague month/year queries) was piloted against
4 events (CPI-21DEC, CPI-22JUN, CPI-23MAR, KXCPI-24NOV) and produced only
wrong-year template-repost candidates, all caught by the admission filter's
year-verification step, terminating all 4 as UNKNOWN. Per explicit direction,
those 4 UNKNOWNs are PROVISIONAL, not final. This v2 replaces v1 with a fixed,
audited fallback ladder before any further search. No source is added ad hoc
per event; the ladder below is exhaustive and applies identically to every
remaining event.

## Bounded acquisition-path audit (why these three rungs, and no others)

Reviewed for legally accessible, reproducible, provenance-preserving routes:

1. **reuters.com direct.** The primary source. P10B's own receipts document
   this is frequently bot-walled (DataDome challenge, HTTP 401) for this
   working environment, but it must still be attempted first per event —
   when it succeeds, it is the most direct provenance.
2. **Approved syndicated Reuters copies — the exact host set P10B already
   reviewed and committed evidence from**: Yahoo Finance (yahoo.com /
   ca.finance.yahoo.com / finance.yahoo.com), kfgo.com, TradingView News
   (tradingview.com/news), Nasdaq (nasdaq.com/articles), Investing.com,
   WMBD, AOL. These carry full Reuters wire text under `provider` JSON-LD
   naming Reuters and/or an explicit "By Reuters" / wire-credit byline —
   already established as legitimate corroboration sources by the reviewed
   P10B bundle, not a new authority model.
3. **Internet Archive Wayback Machine / CDX index, scoped to the exact same
   host set from rungs 1-2.** This is not a new content source — it is a
   time-indexed public retrieval mechanism for recovering historical
   snapshots of pages from the identical already-approved hosts, used only
   because live web search is recency-biased and repeatedly aliases
   month-name queries to the newest same-template repost rather than the
   historical one. Content found this way is still checked against the
   identical admission filter (Reuters attribution, exact year verified in
   body text, prospective tense, cross-host corroboration) — Wayback is a
   *retrieval* route, not a provenance shortcut.

No other route was found reviewable: the repository's internal evidence-store
pattern (`docs/PERPS_SHADOW_RESEARCH.md` and related) is explicitly
disclosed in the P10B manifest as inapplicable to third-party copyrighted
news content, and no other durable archive/blob service is available to this
environment. Live web search (any other generic query) is excluded as its
own rung — it already ran in v1 pilot and is superseded by the exact-date
queries in rungs 1-2 below, not added as a fourth parallel route.

## Per-event inputs (unchanged, outcome-blind)
- `event_ticker`, `reference_month` (YYYY-MM).
- `release_date` = calendar date component of the event's earliest frozen
  `sibling_cutoff` (already-frozen manifest fact, not derived from any
  outcome, CPI value, or Kalshi data).
- `day_before` = `release_date` minus 1 calendar day.
- Full list of that event's `sibling_cutoff` timestamps, for later
  temporal-eligibility annotation only — never a search filter.

Queries use the **exact release date**, not vague month/year, specifically to
reduce the recency-aliasing failure mode observed in the v1 pilot.

## Rung 1 — reuters.com direct
- R1a: `site:reuters.com "{release_date as Month D, YYYY}" consumer prices`
- R1b: `site:reuters.com "{day_before as Month D, YYYY}" consumer prices forecast`
- If a reuters.com URL is found, attempt a direct fetch. Record the outcome
  (200 with content, or bot-wall/401/etc.) exactly as P10B's receipts do.

## Rung 2 — approved syndication hosts, exact-date queries
- R2a: `Reuters "consumer prices" "{release_date as Month D, YYYY}" site:yahoo.com OR site:kfgo.com OR site:tradingview.com OR site:nasdaq.com OR site:investing.com OR site:aol.com`
- R2b: `Reuters "consumer prices" "{day_before as Month D, YYYY}" site:yahoo.com OR site:kfgo.com OR site:tradingview.com OR site:nasdaq.com OR site:investing.com OR site:aol.com`
- Every candidate must still pass the full admission filter (section below),
  including explicit in-body year verification — exact-date phrasing reduces
  but does not eliminate wrong-year aliasing, so the check stays mandatory.

## Rung 3 — Wayback/Internet Archive CDX lookup, same host set
For each host in the rung-2 set, query the CDX API scoped to a narrow window
around `release_date`:
`https://web.archive.org/cdx/search/cdx?url={host}&matchType=domain&from={release_date-2d}&to={release_date+2d}&output=json&filter=urlkey:.*(consumer|cpi|inflation).*&collapse=urlkey&limit=100`
Fetch promising snapshot URLs (via `http://web.archive.org/web/{timestamp}/{original_url}`) and verify per the same admission filter.

Only if all three rungs, attempted in order, produce no admissible candidate
does the event terminate UNKNOWN.

## Candidate admission filter (unchanged from v1, applies at every rung)
A candidate is admissible only if ALL hold:
- Attributed to Reuters (wire credit line, explicit "(Reuters)" dateline, or
  `provider` JSON-LD naming Reuters).
- Explicitly discusses the exact `reference_month` — **verified in the
  article's own body text/dateline, never from the query match or a search
  summary** (v1 pilot precedent: every wrong-year trap was a same-template
  repost that only direct fetch-and-read caught).
- States a forecast for headline CPI, month-over-month, seasonally adjusted,
  nonannualized, in prospective/forecast tense — not a retrospective/actual-
  result report.
- `datePublished` (host metadata) is on/near the expected pre-release wire
  date for that reference month.

## Corroboration requirement (PASS gate) — CONFIRMED, kept as-is after pilot
≥2 independently operated hosts (from rungs 1-3 combined) carrying the same
Reuters wire item with matching `datePublished` (small disclosed clock skew
acceptable) and a byte-identical or substantively identical load-bearing
forecast sentence. A single-host hit is insufficient for PASS, **even when
that single host carries unambiguous schema.org/JSON-LD organization
attribution to Reuters plus a raw "(Reuters) -" dateline** (pilot precedent:
KXCPI-24NOV had exactly this — investing.com, correct date, pre-cutoff,
explicit "Economists polled by Reuters expect headline inflation to increase
0.3% in November" — and still terminates UNKNOWN for lack of a second
independent host). This keeps the evidentiary bar uniform with P10B across
all 42 events; explicit decision confirmed 2026-09-05, not to be revisited
per-event.

## Terminal states
- **PASS**: admissible candidate on ≥2 independent hosts; receipt.json +
  extract.json pair, schema-identical to `docs/reviews/artifacts/cpi-p10b-reuters/<TICKER>/`.
- **UNKNOWN — SEARCHED, NO QUALIFYING OBSERVATION FOUND**: all three rungs
  attempted, no candidate satisfies the admission filter + corroboration gate.
- **FAILURE — ACQUISITION/AUTHORITY FAILURE**: a tooling/systemic problem
  (not "nothing relevant found") prevented completing the search.

## Per-sibling temporal-eligibility annotation (no scoring)
For any PASS, record for each frozen `sibling_cutoff` whether the proven
publication instant is before or after it — a timing fact only.

## Value precision
Record the Decimal value exactly as published, never rounded or promoted.
