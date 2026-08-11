# Engineering Contract

`MASTER_SPEC.md` is the authoritative product specification. Implement its milestones in order and keep `docs/IMPLEMENTATION_STATUS.md` current.

## Non-negotiable safety

- Production writes and autonomous trading default to **off**; never place a real-money order during development or tests.
- The deterministic risk engine, not an LLM, authorizes risk. The signer is isolated from research and web services.
- Use `Decimal` for money, probabilities, prices, fees, quantities, and risk.
- Missing, stale, ambiguous, unreconciled, or unsupported inputs fail closed.
- Never commit credentials, private keys, state, databases, logs, or generated secrets.

## Quality gates

Use Python 3.12+, strict typing, Ruff, pytest, and security/secret scans. Add deterministic tests for every safety invariant and record milestone reviews in `docs/reviews/`.

See `docs/architecture.md`, `docs/risk_policy.md`, `docs/security_model.md`, `docs/source_policy.md`, `docs/model_governance.md`, and `docs/implementation_plan.md`.
