# Source Policy

Use official or explicitly permitted interfaces only; never scrape authenticated products. Every source records ownership, classification, licensing, cost, provenance, publication/receive/ingestion times, revision history, health, and promotion stage.

Lifecycle is Candidate → Shadow → Eligible → Limited Production → Approved, with Quarantined available. New production sources require human approval. Social signals initially route attention only. Backtests use bot ingestion time and real-time data vintages.

## M4 contract documents and settlement sources

Exchange-named series/event sources are recorded as such, not automatically declared independently official.
Market rules, event sources, series sources and contract documents retain separate provenance and precedence.
Contract retrieval is HTTPS-only to an exact allowlist, bounded, non-redirecting and untrusted; disagreement or
failure blocks semantic validation rather than selecting a convenient source.

## M5 external intelligence

M5 permits only official public APIs/streams, approved HTTPS feeds, and explicitly authorized imports. Direct
Polymarket is the core cross-venue source; it has no trading authentication. PredictBuddy is disabled/manual/
authorized-delivery only until an official API exists. X is SETUP_REQUIRED without approved token and cost.
Bluesky Jetstream is discovery-only until the canonical DID/record is verified. Reddit remains SETUP_REQUIRED
pending access review. All are candidate/shadow with production influence zero.

Retention is source-specific: market metadata/trades/manifests may be durable; social content is minimized and
expires according to terms while permitted hashes, lineage, deletion/correction state and audit metadata remain.
No arbitrary URL fetch, scraping, or open social firehose is allowed.
## M7 LLM provider disclosure and retention

M7 sends only immutable public market context and approved public source spans from an EvidenceBundle.
It never sends credentials, signatures, balances, positions, orders, fills, owner sessions, recovery
material, or unrelated account information. OpenAI traffic is restricted to `api.openai.com`; Anthropic
traffic is restricted to `api.anthropic.com`. Provider retention settings and pricing versions are
deployment configuration and must be recorded with each run. Provider cost is **UNKNOWN**, not zero,
when pricing metadata is absent. No provider is configured or live-verified in offline M7 acceptance.
