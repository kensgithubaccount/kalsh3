# CPI-E1-P9B.4R.1 historical taker-fee continuity

This review records the conservative correction to the immutable P9B fee package.
The package freezes the September 2022 direct schedule, the May 2025 court-exhibit
snapshot, the governing filing-rule evidence, and an official date-bounded KEX
filing inventory. It does not treat a mutable filing page or an unbound P8 list as
authority.

## Direct schedule authority

CFTC filing 49335 was received on 2022-09-09 and certified effective 2022-09-22.
Its retained final schedule establishes the general taker formula
`round_up(0.07 * C * P * (1-P))`, rounded to the next cent. The cover, redline,
final schedule, official filing page, and byte hashes remain fixed by reviewed
runtime constants.

## Regulatory continuity authority

The July 2025 retained rulebook contains Rule 3.10, “Dues, Fees, and Expenses
Payable by Members,” with subsections (a) through (d). Subsection (b) states,
in substance, that traders may be charged trading fees in amounts revised from
time to time and reflected on the company website. The rule contains no
subsection (e) and does not itself state a universal pre-implementation filing
obligation. That snapshot therefore cannot prove the governing rulebook text or
filing obligation throughout 2022-09-22 through 2025-05-06.

The official CFTC date-bounded KEX inventory includes filing 49335 and intervening
fee-related filings: the 2024 fee-waiver program and series notifications,
volume-incentive programs, the January 2025 rebate, and market-maker programs.
Those are not silently excluded from actual fee history: their conditional KXCPI
applicability, listing intervals, eligibility, and effect on ordinary crossing
trades remain unresolved. They are excluded only from general taker formula
authority because they are conditional, series-specific, maker-only, withdrawn,
or unrelated to the general schedule. The inventory is retained as a deterministic artifact and its rows
record filing IDs, dates, descriptions, official filing URLs, applicability, and
exclusion rationale. This establishes what was searched and prevents the single
49335 page from masquerading as an exhaustive inventory; it does not assert that
conditional program eligibility can be resolved from P9B.

## Classification and boundaries

Date-only effective evidence is not converted to midnight UTC. Quotes through
2022-09-23 remain unknown; the first unambiguous date is 2022-09-24. The court
exhibit’s 2025-05-06 “Last Updated” date is a locator boundary, not an exact
instant. Quote mapping uses each P9A row’s `candle_end_period_ts`.

The current mechanically derived fee-only coverage is 474 market rows and 60
events: 272 same-formula endpoint-snapshot rows / 31 events, 110 locator-only
rows / 14 events, 92 unknown rows / 15 events, and no mixed-authority rows. P8/P9A/fee
intersection is intentionally deferred to the downstream P10 authority binder;
no P8 event list or quote-usability claim is made here.

## Remaining unknowns

Pre-effective authority remains unknown. Post-2025-05-06 authority remains
locator-only. Maker authority, waiver/rebate eligibility, executable quote
status, truth binding, after-cost evaluability, and profitability are not
established by this package.
