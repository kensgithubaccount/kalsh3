# WN-A2 settlement outcome reconciliation

WN-A2 starts only from an existing persisted WN-A1 attempt ID. It reads the exact
persisted ticker, event, date, series, policy, and source identity; callers cannot
replace those values or provide parsed settlement data.

The exact public Kalshi market response is canonical settlement authority. The
response is acquired with GET only, retains the exact request path, raw body, SHA-256,
observation time, and parsed market, and is accepted only for `finalized` markets with
result, settlement value, and settlement timestamp. The historical/live choice is
made from Kalshi's public `/trade-api/v2/historical/cutoff` market-settled cutoff.

TWC is deliberately not part of this P1 implementation. If added, the bounded route
is `https://weather.com/kalshi/api/climate/primary?date=YYYY-MM-DD`, with exact
`CLIMDW` ↔ `KMDW`/`MDW` station mapping, and corroboration can never outrank Kalshi.

The separate fixed-location outcome SQLite journal is append-only. It writes START
before acquisition, then immutable evidence and terminal result rows; duplicate
reconciliation and update/delete are rejected. WN-A1 bytes and identity remain
untouched. A later controlling Kalshi correction must be appended as a new observation,
never silently overwritten.

States are `SETTLED_MATCHED`, `NOT_FINAL`, `CONFLICT`, `SOURCE_UNAVAILABLE`, and
`EVIDENCE_INVALID`. This lane is research-only, has zero production influence, makes
no profitability claim, and adds no settlement-window inference. WeatherNext remains
forecast evidence only; WN-A1 remains frozen.

## WN-A2-E1 — versioned settlement-boundary semantics repair

The previous RANGE predicate used an upper-exclusive bound. The finalized
`KXHIGHCHI-26SEP24-B68.5` response explicitly reports `expiration_value=69.00`
and YES for the structured 68–69°F range. The current KXHIGHCHI series points
to [GLOBALTEMPERATURE terms](https://assets.kalshi.com/contract_terms/GLOBALTEMPERATURE.pdf),
whose comparator section makes BETWEEN inclusive at both ends and ABOVE/BELOW
strict; its precision section requires the full source-reported precision.
Five preselected consecutive completed Chicago events (Sep 21–25, 2026), all
30 markets, were archived and checked rather than generalizing from one winner.
The per-market TWC source remains controlling over the terms' generic source default.

`wn_a2_temperature_bucket_semantics.py` now explicitly implements:

- RANGE: `lower <= expiration_value <= upper`.
- LT: `expiration_value < cap_strike`; the legacy persisted `contract_lower`
  field holds this cap threshold. Display text such as “65 or below” is not
  substituted for the structured threshold 66.
- GT: `expiration_value > floor_strike`; “74 or above” is not substituted
  for the structured threshold 73.
- No new rounding, integer mapping, decimal quantization, conversion, or
  nearest-bucket assignment. EXACT or future transformation wording is not
  supported and fails closed. Upstream source reporting conventions are not
  newly inferred; the finalized Kalshi `expiration_value` is already canonical.

Before scoring, the reviewed current-family rule parser is reused to check
date, structured comparator, and bounds against the persisted contract.
Nonempty secondary rules must match the exact reviewed standard text shared by
all 30 archived markets (SHA-256
`c1f11eaf372267f2e69ca8ba131916928052ec5e92138e45c16e197f2d4bb507`).
That text warns about preliminary readings but does not transform the finalized
underlying. Unknown or contradictory secondary overrides fail closed rather
than being ignored; absence/empty secondary rules add no override to the
otherwise exact reviewed primary rule.
Nonfinite values, invalid ranges, changed rules/strikes, missing exact underlying
values, ambiguous tail shapes, and payout/result mismatches fail closed.
`settlement_value_dollars` is the binary payout, never a temperature fallback.
The source identity for new evidence includes the explicit version
`wn-a2-globaltemperature-inclusive-full-precision-v1`.

Journal schemas and existing immutable persisted record identities are unchanged.
Stored-result loading still verifies original bytes/identities without rescoring
under a new predicate. If historical results need reinterpretation, append a
separate repair observation bound to the old identity; do not update/delete or
rerun an existing registration. Event #1's packet, acceptance, evaluation freeze,
forecast receipt, settlement/outcome record, and their legacy conflict diagnostic
remain byte-for-byte frozen. No forecast-model or WN-A1 probability semantics
have been changed. No production influence, trade, PR, or scheduler is introduced.

The old synthetic WN-A2 test fixture put temperature 72 into a field named
`settlement_value_dollars` and omitted `expiration_value`; it is now corrected
to model real API fields. The original fixture inputs and pre-repair states
are preserved in the append-only repair evidence, along with stricter-gate
classification changes and correction guidance.

Authority, defect, replay, tests, and independent-review evidence:
`experiments/wn_a2_e1_boundary_repair_v1/`.
