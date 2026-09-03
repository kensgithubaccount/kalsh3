# MM-A1 Autonomous Market-Making Research Boundary

## Decision

MM-A1 adds a **research-only, passive market-making evidence lane**. It does not place paper,
demo, or real orders and it does not assert that market making is profitable. Its purpose is to
turn validated structural fair values and authoritative live market evidence into immutable
shadow quote proposals that can be evaluated by the existing M11 fill simulator.

The intended destination is bounded live autonomy. Paper/demo execution is a required validation
gate under the master specification, not the product objective and not an authority granted here.

## Reused canonical authority

MM-A1 does not create a second book, fee, rules, or fill model:

- M27A `MarketEconomicsEvidence` supplies the canonical normalized book and current fee regime.
- `validate_authoritative_economics_market_binding` independently re-parses the retained exact
  market snapshot and binds the market/event/rules identity to that economics evidence.
- M11 `simulate_maker` supplies queue, latency, partial-fill, cancel-race, pause/gap, fee, markout,
  and settlement behavior in optimistic, base, and adverse scenarios.
- M13 remains the sole deterministic risk authority. MM-A1 does not import it because MM-A1 cannot
  request execution.

A bare caller-supplied `FeePolicy`, book, rules hash, or claimed fair probability is insufficient.
Any mismatch produces an explicit abstention.

## Fair-value contract

A quote requires a content-addressed `FairValueCurve` with:

- at least two distinct sibling thresholds in the same event;
- monotonic lower, point, and upper YES probabilities;
- model, calibration, cohort, and evidence-manifest identities;
- a nonempty independent validation receipt;
- explicit issuance and expiry timestamps; and
- `ELIGIBLE_SHADOW_RESEARCH`, never production eligibility.

The planner uses the conservative edge of the interval: `lower_yes` for YES and
`1 - upper_yes` for NO. This makes model uncertainty reduce quoting rather than expand it.

## Quote policy

The default policy is content-addressed and fixed to one hypothetical contract per side:

- 5% minimum expected net edge (inside the reviewed 4%-8% range);
- exact current maker fee from bound M27A evidence;
- 1% adverse-selection reserve;
- 1% latency/volatility reserve;
- 0.5% capital-turnover reserve;
- inventory skew of 0.5 cents per contract, capped at 2 cents;
- two-second book/economics and inventory freshness;
- fifteen-minute close guard;
- two-sided quotes required unless the sole surviving side strictly reduces inventory.

Every proposal records `post_only=true`, `cancel_order_on_pause=true`, order-group required,
self-trade prevention, `exchange_order=false`, and `production_influence=0`. These are evidence
attributes, not an exchange request.

## Fail-closed abstention gates

The planner abstains for any invalid or stale curve, policy, market snapshot, economics object,
economics binding, book, fee regime, inventory snapshot, rules/specification identity, sequence,
market state, source health, own-order knowledge, inventory limit, close guard, competitiveness,
or net-edge hurdle. It also abstains when only a new-risk side survives.

## Evaluation

`ShadowAttemptReceipt` binds a proposed quote to one canonical M11 maker simulation. Filled
attempts require exact 1-second, 30-second, and 5-minute markouts for every fill; optional
settlement must belong to the same simulated order. Unknown execution cannot carry conclusive
economics. Summary objects permanently report:

- `profitability_claim=NOT_ESTABLISHED`;
- `production_eligible=false`; and
- `production_influence=0`.

This prevents a favorable offline result from silently becoming trading authority.

## Path to bounded live autonomy

1. **MM-A1 (this change):** independently review the quote/evidence contract and adversarial
   abstentions.
2. **MM-A2:** run bounded public-read shadow observation, retaining quote opportunities, M11
   optimistic/base/adverse outcomes, markouts, no-fills, adverse selection, inventory paths, and
   exact fee assumptions. Do not submit orders.
3. **MM-A3:** preregister advancement criteria and require positive base and adverse after-cost
   economics across independent events, not only quotes or optimistic fills. Include multiple-
   testing control and capacity/turnover analysis.
4. **MM-A4:** use the existing demo/paper state machine only as a reliability and reconciliation
   gate. It is not evidence of real queue position or real profitability.
5. **MM-A5:** only after all repository promotion gates, human authorization, deterministic M13
   approval, credential/signer readiness, and reconciliation, consider one-contract supervised
   live canaries with hard kill switches.
6. **MM-A6:** bounded autonomy remains OFF until at least the master-spec real-fill, settled-
   position, stability, and audit requirements are met. Autonomous market creation is out of
   scope; this is autonomous quoting in existing eligible markets only.

No stage guarantees profit. Advancement must be earned from prospective, after-cost evidence.

## Verification

Focused tests cover valid two-sided proposals; monotonic fair-value validation; stale, inactive,
paused, gap, close, source, rules, specification, inventory, fee, economics-binding, and policy
tampering; one-sided inventory reduction; suppression of one-sided new risk; narrow-spread edge
rejection; and exact reuse of M11 fills, fees, markouts, and settlement.
