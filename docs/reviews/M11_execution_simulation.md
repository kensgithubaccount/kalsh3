# M11 Event Backtests and Execution Simulation Review

## Acceptance report

| Gate | Status | Evidence / limitation |
|---|---|---|
| M11 code | OFFLINE VERIFIED | Immutable research artifacts, replay engines, analytics, persistence, and UI fixtures pass. |
| Taker arrival simulation | OFFLINE VERIFIED | Uses the latest valid replay book at simulated exchange arrival, never the candidate-time book. |
| Maker queue simulation | MOCK VERIFIED | Sequence/trade fixture replay with scenario-governed aggregate queue assumptions. |
| Queue-position quality | OFFLINE VERIFIED | Hypothetical orders reject `OBSERVED_OWN_ORDER_QUEUE`; aggregate depth is explicitly an assumption. |
| Partial fills | OFFLINE VERIFIED | Exact `Decimal` fill, remainder, expiry, settlement, and fee accounting. |
| Cancel/fill races | OFFLINE VERIFIED | Fills before simulated cancel effectiveness remain recorded. |
| Latency | OFFLINE VERIFIED | Versioned assumed signal, compute, scheduling, network, exchange, and cancellation components. No assumed value is called measured. |
| Fee integration | MOCK VERIFIED | Effective fixture M10 fee policy is applied per fill; live formula remains unverified. |
| Fee rounding | MOCK VERIFIED | Cumulative order fee is differenced across partial fills rather than independently rounding each fill. |
| Adverse selection / markouts | OFFLINE VERIFIED | Outcome-normalized markouts are distinct from settlement results. |
| Information decay | OFFLINE VERIFIED | Candidate, arrival, fill, and markout times remain distinct in artifacts. |
| Fill model | UNVALIDATED | Calibration targets exist; synthetic results cannot promote an empirical model. |
| Slippage validation | MOCK VERIFIED | Candidate estimate versus simulated arrival fill fields exist; no real fills. |
| Walk-forward | OFFLINE VERIFIED | Chronological training, validation, and untouched promotion periods are enforced. |
| Event grouping | OFFLINE VERIFIED | A related event cannot occur in multiple evaluation periods; effective sample is event-level. |
| Multiple-comparison control | OFFLINE VERIFIED | All tested variants and the holdout/FDR method are persisted. |
| Optimistic / base / adverse | OFFLINE VERIFIED | Three immutable, plausible, versioned assumption policies are mandatory. |
| Base + adverse advancement | OFFLINE VERIFIED | Both must be positive and sample, drawdown, concentration, stability, fidelity, and health gates must pass. |
| Cross-venue one-leg risk | MOCK VERIFIED | Independent arrivals and one-leg/reprice/stale/pause states; explicitly hypothetical and non-atomic. |
| Replay-gap safety | OFFLINE VERIFIED | A maker gap becomes `EXECUTION_OUTCOME_UNKNOWN`; candles cannot prove queue fills. |
| Capacity / drawdown | OFFLINE VERIFIED | Research-only capacity points and path-dependent drawdown primitives; no recommended size or capital authority. |
| Large-scale test | OFFLINE VERIFIED | Deterministic streaming fixture covers 100,000 attempts and 5,000 events without retaining the corpus. |
| UI | OFFLINE VERIFIED | Fixture/template assertions cover honest labels and empty state; screenshot acceptance remains PENDING. |
| Real execution observations | NOT VERIFIED | NONE; no live/demo credentials, hypothetical orders, or observed real queue positions. |
| Real strategy evidence | INSUFFICIENT REAL EVIDENCE | Synthetic/historical machinery is not proof of profitability. |
| Production influence | NONE | Enforced in artifacts and persistence; no mutation/signer/risk import or method. |
| Human acceptance | PENDING | Owner review and visual browser QA remain outstanding. |

## Cross-functional review and resolved findings

- **Trader:** Taker fills use arrival-time books. Maker fills respect arrival, side, price, queue ahead, partial fills, finite lifetime, and cancel races. Aggregate public levels are never described as an exact participant queue.
- **Quant:** Frozen M10 candidates are inputs and never rewritten. Unfilled attempts remain in evaluation; optimistic results cannot rescue failing base or adverse economics.
- **Market microstructure:** Trade depletion and ambiguous level reductions are separate. Level reductions receive only scenario-configured cancellation credit and are never themselves fills. Duplicate sequence flow is ignored.
- **Data science / ML:** Partial fills do not inflate event sample size. Chronological periods, event-group purging, variant manifests, concentration, and effective samples prevent common selection/leakage errors. Fill models remain unvalidated.
- **Finance:** Prices, quantities, fees, P&L, committed capital, and drawdowns use exact `Decimal`. No-fill attempts receive no fabricated P&L. Capacity is not sizing.
- **Data engineering:** Policies, runs, datasets, orders, fills, gaps, and calibration targets retain content hashes, replay sequences, time, and lineage sufficient for deterministic reconstruction.
- **Security / SRE:** The namespace contains no signer, gateway, write credential, financial-risk mutation, or production-order path. Gaps, stale/inactive books, insufficient fidelity, and unverified fees fail closed.
- **Product / UX / CFO:** The view places optimistic, base, and adverse outcomes side by side; calls every result historical simulation rather than real trading; and exposes fill, slippage, fee, markout, drawdown, capacity, and failure reasons rather than a headline return.

M11 creates simulated research objects only. It does not place, amend, cancel, authorize, size, or recommend an exchange order.
