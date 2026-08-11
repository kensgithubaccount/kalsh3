# M7 Document + LLM Evidence Review

## Acceptance state

- **Code, fixture provider, structured-output normalization, validation, replay safety, cost controls, 10k eval and UI:** OFFLINE VERIFIED.
- **OpenAI and Anthropic adapters:** MOCK VERIFIED; live providers NOT VERIFIED and not configured.
- **Production influence:** NONE. **Human acceptance:** PENDING.

## Cross-functional findings

- **Security:** Prompts accept only reconstructible public bundles. Documents are delimited hostile data; provider requests expose no browsing/action tools, hosts are allowlisted, and evidence code has no signer, risk, mutation, execution, or account import. Detection supplements—not replaces—architectural isolation.
- **Quant / trader:** Source assertions, model inference, forecasts, opinions, corrections, and contract relations remain distinct. Relations are semantic routing, never probability. Exact thresholds and required fidelity fail closed.
- **ML / data science:** Development and held-out fixture labels remain distinct; model, prompt, schema, bundle and run mode are versioned. Unsupported-material-claim, citation, numeric, abstention and injection metrics expose false confidence and drift.
- **Data engineering:** Bundles, runs, attempts, claims, citations, validations, contradictions and cache entries are immutable/content-addressed. Corrections supersede rather than erase and enter replay only when available.
- **Product / UX:** Pages distinguish raw source, validated extraction, contradiction and ambiguity and explicitly say there is no probability, edge, recommendation, or production influence.
- **SRE:** Provider, schema, citation, rate and cost failures are independent optional-service states. Inference is background queued; pages never invoke a provider.
- **CFO:** Deterministic cache keys prevent duplicate immutable requests. Unknown pricing is displayed as UNKNOWN rather than zero; request/token/cost/concurrency budgets pause optional research only.

No chain-of-thought storage, source promotion, forecasting, portfolio sizing, production write, or execution capability was introduced.
