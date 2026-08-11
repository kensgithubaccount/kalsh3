# Kalshi Production v3

A safety-first prediction-market research, learning, portfolio, risk, execution, and operations platform. The objective is realistic long-run after-cost expected value subject to capital survival, calibration, uncertainty, liquidity, reliability, compliance, and complete auditability. Profit is never guaranteed, and **do not trade** is a valid outcome.

The architecture keeps market intelligence separate from deterministic capital authorization. PostgreSQL stores canonical state, Redis provides transient coordination, NATS JetStream carries events, and S3-compatible storage preserves raw evidence. Forecasts use executable market prices as a baseline and measure whether independent evidence adds point-in-time, out-of-sample value.

## Safety state

- Production writes default to **off** and every restart is disarmed.
- LLM output never directly authorizes or sizes a Kalshi order.
- Production write credentials are not necessary for research, forecasting, read-only monitoring, or the dashboard.
- Only an isolated signer may access a write-capable key; web, research, and execution gateway processes cannot.
- Deterministic risk and reconciliation controls fail closed.

## Development

Requires Python 3.12+, `uv`, Docker, and Docker Compose.

```bash
make bootstrap
make verify
docker compose config
```

Copy `.env.example` to a local ignored `.env` only for development values. Never commit credentials or private keys. The base Compose file keeps data services on internal networks; the development override binds them only to loopback.

Authoritative requirements are in `MASTER_SPEC.md`; durable engineering guidance is in `AGENTS.md`; architecture, risk, security, source, model, activation, and operations policies live under `docs/`. Milestone progress is recorded in `docs/IMPLEMENTATION_STATUS.md`.

## Current limitations

The repository is under milestone-driven implementation. External account access, demo verification, production read verification, and any production-write acceptance require later owner-controlled credential setup. No real-money verification is performed by development automation.
