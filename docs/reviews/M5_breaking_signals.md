# M5 Breaking Signals + External Intelligence — Cross-Functional Review

## Acceptance

Code, schemas, source models, public Polymarket fixtures, source adapters, dedupe/matching/reaction measurement,
connector isolation, archive boundaries and UI are offline verified. No live external feed was connected.
Every source and signal has exactly zero production influence and terminates in SHADOW RESEARCH DATA.

## Review and corrections

- **Trader / product / UX:** Breaking Now says leads are research-only, displays descriptive verification and
  noise states, and always shows trading influence NONE. Cross-venue differences are observations, never edge,
  arbitrage or expected profit. Rumors cannot appear as primary confirmations.
- **Quant / ML / data science:** four timestamps remain distinct; executable bid/ask snapshots—not midpoint—are
  captured at detection and scheduled offsets. Lead/lag is descriptive and not causal. Independent source-chain,
  duplicate, correction/deletion, relevance and reaction fields support later no-leakage M9 evaluation.
- **Data engineering / SRE:** complete cursor scans detect loops/partial pages. Connector queues are bounded;
  overflow opens a gap and degrades only that connector. Circuit backoff is independent. Raw observations are
  immutable and archive manifests carry source-specific retention.
- **Security:** official/authorized adapters only. RSS is exact-host HTTPS allowlisted, SSRF/redirect/type/size
  guarded, and XML/content remains untrusted and escaped. X tokens are repr-hidden. PredictBuddy cannot scrape
  or claim an API. Static tests prohibit risk/signer/execution imports and external mutation verbs.
- **CFO / market research:** direct Polymarket is core and free; X/PredictBuddy remain setup/cost gated. Direct
  versus vendor ingest time and added metadata are measurable, enabling an empirical redundancy decision.
- **Privacy/compliance:** stable IDs and minimal display identity are retained only for provenance/dedupe;
  source-specific retention and deletion states avoid unnecessary indefinite social-content storage.

No material offline issue remains. Live and human acceptance claims remain withheld.
