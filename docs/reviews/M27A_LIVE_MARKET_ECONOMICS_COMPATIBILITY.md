# M27A Live Market Economics Compatibility Review

Status: implementation candidate, focused offline verification complete, independent review pending.

M27A is research-only. It introduces no forecasting, Event Edge decision, `TradeCandidate`, ranking,
capital, order, scheduler, autonomy, or production-execution path. Every new evidence/policy object has
production influence fixed at `Decimal("0")`.

The live compatibility boundary treats explicit complete `price_ranges` as tick authority, accepts the
four observed August 2026 structures plus unknown descriptive labels with valid ranges, and rejects 0/1
as executable raw bids. Broad top quotes remain discovery-only. Exact batch orderbooks are fetched only
through a fixed-origin, GET-only, exact-read client and normalize into M10's existing `NormalizedBook`.

Current fee resolution uses a complete Event override when present and otherwise current Series metadata.
Partial, malformed, flat, and unknown regimes fail closed. Fee-change observations are preserved but do
not rewrite current Series truth or reconstruct historical fees. The repository policy
`kalshi-event-fees-2026-07-07-v1` represents the reviewed economic fee regime effective 2026-07-07 and
cites the authoritative Kalshi Fee Schedule. It carries separate taker and maker coefficients; ordinary
quadratic markets explicitly have no maker fee.

TAKER_NOW walks YES and NO depth independently. It records gross cash cost, theoretical fee, and
centicent rounding. Because a pre-trade snapshot does not reveal eventual fill fragmentation or exchange
accumulator/rebate behavior, it does not claim an exact final fee or conservative final total when no
mathematically justified bound is available.

Independent review should verify coefficient-source evidence, exact live response envelopes, retry and
redirect behavior, content identities, temporal parent matching, and absence of any execution coupling.
