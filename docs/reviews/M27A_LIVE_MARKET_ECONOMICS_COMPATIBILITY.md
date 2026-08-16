# M27A Live Market Economics Compatibility Review

Status: implemented, independently reviewed, and bounded production read-only live accepted.

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

Each economics evidence object is self-contained for deterministic TAKER_NOW replay: it immutably binds
the exact normalized bid depth, authoritative price ladder, resolved fee regime, fee policy, source
identity, and observation time used for both sides. Evidence creation verifies those inputs against the
legacy provenance fields and stored YES/NO results, and replay requires no network, database, clock, or
external archive.

## Production read-only live acceptance

Acceptance ran at repository head `d8ca7db580be18fef63bb8e0d36e4be785b583fc` with requested
hypothetical quantity `0.01`. The acceptance evidence is
`~/.kalsh3/evidence/m27a-live-acceptance-20260816-033036.json`, with JSON SHA256
`ca83bc6b44f46b234d6e8dd418d98adca2bc953d84325d177ff6f8c66e0ca199` and acceptance-log SHA256
`08a985fe810c3b32b3202a9527fa4da2225e70103960216cd2abfb2ee51d6a67`.

The bounded acceptance used one authenticated exact-read batch orderbook GET and current
Market/Event/Series point reads. It performed no production writes, production influence remained exactly
`Decimal("0")`, and trading remained locked/off.

| Representative | Market | Series | Category | Structure / minimum step | Observed depth | Fee resolution | TAKER_NOW / replay |
|---|---|---|---|---|---|---|---|
| CENT | `KXUFCFIGHT-26AUG15MAKMGI-MGI` | `KXUFCFIGHT` | Sports | `linear_cent` / `0.0100` | Fractional: yes | `current_series`; `quadratic`; multiplier `1` | YES and NO available; self-contained exact replay PASS |
| SUBPENNY | `KXGOVFLNOMR-26-JFIS` | `KXGOVFLNOMR` | Elections | `tapered_deci_cent` / `0.0010` | Exact subpenny: yes; fractional: yes | `current_series`; `quadratic`; multiplier `1` | YES and NO available; self-contained exact replay PASS |
| FRACTIONAL | `KXPGATOUR-FESJC26-SSCH` | `KXPGATOUR` | Sports | `tapered_deci_cent` / `0.0010` | Exact subpenny: yes; fractional: yes | `current_series`; `quadratic_with_maker_fees`; multiplier `1` | YES and NO available; self-contained exact replay PASS |

All representatives used fee policy `kalshi-event-fees-2026-07-07-v1` and the `current_series`
fee-resolution path. The Event override path has real M26H.3 archived shapes and focused/adversarial test
coverage, but was not separately exercised by this bounded production acceptance. This acceptance does
not establish that every fee-resolution branch was live accepted.

Pre-fill final exchange fee remains `UNKNOWN`, by design, and maker opportunity economics remain
unsupported. This acceptance makes no forecasting, fair-value, edge, profitability, ranking, capital
allocation, `TradeCandidate`, `DecisionReceipt`, `RiskIntent`, execution, order-activity, or
trading-readiness claim. M27A remains research-only with production influence exactly `Decimal("0")`.
