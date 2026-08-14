# M26B — Persisted Agent Decisions, Opportunity Attribution, and Current Beliefs

## Result and architecture

M26B turns existing immutable research evaluations into durable, owner-readable decisions without
adding a collector, scheduler, execution path, credential dependency, capital allocation, sizing,
or autonomous trading. The audit found three applicable conventions: content-addressed immutable
research objects in the opportunity engine, dedicated append-only SQLite evidence stores in Perps
shadow research, and a server-rendered dashboard whose local `StateStore` owns read projections.
M26B follows those conventions rather than adding a generic mutable repository layer.

`services/agent_control_center/store.py` is a dedicated append-only SQLite receipt store. It may use
the dashboard database file, but it owns only `decision_receipts`; the dashboard reads it through
the agent-control-center API. No production-execution module imports this store. WAL, full
synchronous commits, an integrity check, a primary key, an explicit logical-source uniqueness
constraint, and database triggers provide durable fail-closed behavior. No update or delete method
exists, and direct SQL `UPDATE`/`DELETE` is rejected by triggers.

## Receipt identity and idempotency

A receipt ID is SHA-256 over the canonical tuple
`("m26b", agent_id, agent_version, source_kind, source_id)`. The timestamp is deliberately absent:
changing a timestamp cannot hide a replay of the same source evaluation. For Event Edge the source
is an immutable `TradeCandidate.candidate_id`. Cross-Market uses a separate content-bound source
identity described below; its caller-supplied observation ID is provenance, not authoritative
identity.

- Same observation/decision: same agent, version, source kind, source ID, and byte-identical
  canonical receipt. Append returns `False` and does not add a row.
- Valid later decision: a genuinely new immutable source evaluation with a new source ID, even for
  the same instrument.
- Identity collision: either the receipt ID is rebound to another source or the globally unique
  logical `(source kind, source ID)` is reused by another agent or with any different attribution,
  evidence, or canonical content. Append fails closed in application logic and at a unique SQLite
  index. Agent ID is deliberately not part of source uniqueness.

If an unshipped legacy M26B-shaped database already contains globally duplicated source identities,
unique-index initialization raises a typed `DecisionReceiptStoreError`. The store neither deletes
nor deduplicates conflicting history.

Canonical JSON is retained together with its SHA-256 integrity hash. All Decimals are stored as
strings, including trailing zeros; timestamps must be UTC and retain microseconds. Retrieval orders
by `created_at DESC, receipt_id DESC`, providing a deterministic tie-break. Indexed fields support
receipt, agent, instrument, time, decision, recent/latest, and grouped-count queries.

New receipt construction and historical restoration have different authority rules. The normal
`DecisionReceipt(...)` constructor remains strict against the current registry: registered agent,
exact current version, available implementation, and non-disabled autonomy are mandatory. The
store alone uses a private historical restoration function. It validates exact fields and types,
UTC time, Decimal parsing, decision enum, zero influence, structural invariants, canonical JSON,
stored content hash, row metadata, and receipt/source identity, but does not consult current agent
membership, version, availability, or autonomy. Thus a once-valid immutable receipt survives agent
version bumps, disablement, unavailability, or retirement without granting authority to create a
new receipt. Restored objects remain frozen. Corruption fails closed.

Current authority governs every new write independently of historical structural validity.
`OpportunityAttribution.__post_init__` uses one shared authority validator and rejects a new
attribution unless its current agent exists, is `AVAILABLE`, and is not `DISABLED`.
`DecisionReceiptStore.append()` invokes the same validator again so an attribution formed before a
registry disablement—or a test-only object that bypasses the frozen attribution constructor—cannot
be persisted afterward. This defense does not run in historical restoration, `get`, `recent`, or
`latest`; old reads therefore remain registry-independent. A structurally valid historical Perps
or Portfolio receipt can be audited but cannot form or persist a new attribution.

## Explicit attribution and implemented adapters

`OpportunityAttribution` is a frozen contract containing source kind, immutable source ID, explicit
agent ID, exact evidence-reference tuple, resulting receipt, and zero production influence. It
validates that agent, receipt, evidence, and deterministic identity agree. Attribution is never
inferred from display names or prose.

M26B implements only the two mappings supported by current structured domain facts:

- Event Edge: `RESEARCH_CANDIDATE` and `HIGH_PRIORITY_RESEARCH_CANDIDATE` map to `WOULD_TRADE`;
  `WATCH` maps to `NO_TRADE`; `INCOMPLETE` maps to `INSUFFICIENT_EVIDENCE`; rejected evaluations
  with evidence/data/semantic failures map to `INSUFFICIENT_EVIDENCE`; close-time and correlation
  guard failures map to `BLOCKED_BY_RISK`; other rejected economics map to `NO_TRADE`. Original
  rejection reason codes are retained. Executable price, model probability, raw difference, exact
  fee, exact frozen-economics slippage, and conservative expected value are copied. The adapter
  requires `TradeCandidate.expected_slippage` to equal the `OutcomeEconomics.expected_slippage`
  used in conservative EV, then persists the economics value without recomputation. The
  opportunity engine has no separate structured risk-check result, so `risk_check_results` remains
  empty; rejection codes exist only in `rejection_reasons`. Confidence and the separate fair-value
  field remain `None` because the source does not establish them separately.
- Cross-Market: a structured candidate maps to `WOULD_TRADE`, watch to `NO_TRADE`, and incomplete or
  semantic mismatch to `INSUFFICIENT_EVIDENCE`. The caller must explicitly provide instrument,
  UTC evaluation time, and evidence references because the existing observation does not contain
  them. Discrepancy, both venue fees, slippage, semantic reserve, leg-risk reserve, venue-state
  finding, reference overlap, and semantic match are preserved. It adds no multi-leg execution or
  reservation capability.

Cross-Market source identity is SHA-256 over canonical JSON with domain separator
`m26b-cross-market-source-v1`, sorted keys, and exact Decimal strings. It binds every structured
`CrossVenueOpportunityObservation` field—including semantic, risk, cost, depth, skew, overlap,
research state, and zero influence—plus instrument, UTC evaluation timestamp, and ordered evidence
references. The caller-supplied `observation_id` is also bound as caller-attested provenance, but it
does not determine identity by itself. Reusing that raw ID after changing any source-package fact
necessarily produces a different content-bound source ID. This proves package immutability, not the
real-world truth of caller-supplied metadata.
Evidence-reference order is intentionally identity-sensitive and regression-tested because the
ordered provenance package is retained exactly.

Breaking Signals has validated signals but no structured cost-adjusted trading conclusion;
Resolution supports research eligibility but has no standalone persisted evaluation contract with
the required receipt facts; Learning creates governance proposals, not trade decisions. They do not
produce receipts. Perps and Portfolio remain `UNAVAILABLE / DISABLED` and cannot construct receipts.

## Current belief and freshness

`CurrentAgentBelief` is a frozen projection of `AgentDefinition + latest persisted receipt +
FreshnessPolicy + as_of`. It is never independently persisted. No receipt renders “No decisions
yet.” A receipt renders its instrument, conclusion, UTC time, established economics, evidence,
reasons, deterministic explanation, and zero authority. The dashboard injects explicit display-only
maximum ages of one hour for Event Edge and five minutes for Cross-Market. An agent without an
explicit freshness rule fails closed as stale if it somehow has a receipt. Staleness changes only
the owner-facing CURRENT/STALE label and triggers no action.

## Dashboard behavior

The Agents roster displays the latest persisted conclusion, instrument, current/stale state, and
deterministic explanation, or “No decisions yet.” Agent detail displays the current belief and
ordered receipt history with timestamp, instrument, conclusion, after-cost edge when present,
evidence references/count, rejection reason, and explanation. Every receipt card says “Research
only · No order authorized · Production influence 0.” Performance remains “Not enough evidence.”

Overview reports actual recent persisted receipts rather than an invented best idea. Opportunity
rows are joined to receipt attribution only by explicit source kind and source ID. Rows without that
record display “Unattributed research.” Ranking and economics are unchanged. All receipt-derived
text is HTML escaped. Evidence references that look like PEM, authorization headers, API keys, or
private-key material are rejected before persistence; references remain identifiers rather than raw
payloads. If receipt hash, canonical content, metadata, or source identity validation fails, the
Overview and agent pages explicitly show “Decision history unavailable”; corrupt evidence is not
silently converted to an empty history or rendered as trusted data.

## Verification and safety boundaries

The deterministic test/replay path constructs immutable synthetic opportunity outputs only inside
tests. Tests cover canonical round trip, Decimal and UTC microsecond fidelity, deterministic
ordering, replay idempotency, logical collision rejection, append-only triggers, zero influence,
query counts, both adapter mappings, retained rejection reasons, absent invented values, disabled
agents, current/empty/stale beliefs, deterministic explanations, roster/detail/Overview rendering,
explicit and unattributed opportunity rows, adversarial HTML escaping, and the unchanged performance
boundary. Review-correction tests additionally cover registry version/availability evolution,
strict new-receipt rejection, tampered historical rows, global cross-agent source uniqueness,
Cross-Market content sensitivity and raw-ID reuse, non-duplicated Event Edge risk semantics,
authoritative slippage, single-row opportunity attribution, and explicit corrupt-history UI states.
The final adversarial test structurally restores unavailable Perps/Portfolio receipts, proves new
attribution rejection, bypasses attribution construction in test-only code, proves append-time
rejection and zero inserted rows, and confirms historical Event Edge reads survive later disablement
while new Event Edge attribution and persistence are blocked.

There is no performance ledger, win rate, Sharpe, P&L, realized edge, calibration score, ranking,
capital allocation, position sizing, or learning mutation. No production data or credential is
required. No production network call is made. No order is authorized. Production execution remains
**DISARMED**, trading remains **OFF**, and every definition, attribution, receipt, persisted row,
and current-belief projection enforces `production_influence == Decimal("0")`.

## Known limitations

M26B supplies adapters and persistence but deliberately adds no runtime collector or autostart;
receipts appear only when an existing offline/replay research process explicitly calls an adapter
and appends its attribution. Cross-Market observations lack native instrument/time/provenance fields,
so those inputs remain mandatory at the adapter boundary. Breaking Signals, Resolution, Learning,
Perps, and Portfolio do not produce receipts. Outcome joins and agent performance evaluation remain
future milestones.
