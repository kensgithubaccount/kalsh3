# M4 Contract / Settlement Intelligence — Cross-Functional Review

## Acceptance

Code, schema, deterministic parsing, versioning, source/document boundaries, settlement models, queue, UI and
35-case fixture inventory are offline verified. Live Kalshi semantics and contract-document retrieval remain
unverified. M4 produces contractual truth only: no forecast, probability, edge, opportunity, or execution.

## Review findings and corrections

- **Trader:** payout models are explicit. Scalar/sub-cent and MVE contracts are unsupported rather than forced
  into $0/$1 semantics. Inclusive/exclusive comparator language, threshold/strike conflicts, timezone/deadline,
  early-close and source conflicts block VALID, preventing a later opposite-side or wrong-deadline trade.
- **Quant / ML / data science:** source observations, exchange determinations and final settlement records are
  separate immutable histories. Only finalized, reconciled records can become training labels; amendments and
  disputes never overwrite earlier state. Semantic versions carry point-in-time hashes and provenance.
- **Data engineering:** market, event, series and document inputs remain separate and hashed. Material semantic
  changes supersede and stale old specs; normal prices do not churn semantic versions. Contract-term changes do.
- **Security:** the document connector permits only exact allowlisted HTTPS hosts, disables redirects, bounds
  timeout/size/type, hashes content, and treats it as untrusted data. It cannot broaden network access or issue
  instructions. M4 has no Kalshi signer, credential, or mutation path.
- **SRE:** URL failures leave semantics ambiguous/stale, never implicitly valid. Validation runs/issues and
  invalidations are durable. Settlement monitoring is priority-based, not first-100 alphabetical.
- **Product / UX:** Market Detail states YES/NO, authority/source, measurement/threshold, time/timezone, policy
  handling, payout/status/issues and provenance. Unsafe status visibly says later strategy must fail closed;
  no probability or edge appears. Validated is not labeled eligible.
- **Legal/compliance:** exchange-provided layers and exact names remain distinct; precedence is never invented.
  The system records contractual interpretation and does not claim independent sources are legally controlling.
- **CFO:** deterministic blockers reject obviously unsupported markets before any future LLM/document expense;
  retrieved documents are versioned and bounded.

No material offline issue remains. Live and human acceptance claims remain withheld.
